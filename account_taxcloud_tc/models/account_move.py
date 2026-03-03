# Copyright (c) 2015-2023 Odoo S.A.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.


import datetime
import logging

from odoo import _,api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round

from .taxcloud_request import TaxCloudRequest

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    # Used to determine whether or not to warn the user to configure TaxCloud
    is_taxcloud_configured = fields.Boolean(related="company_id.is_taxcloud_configured")
    # Technical field to determine whether to hide taxes in views or not
    is_taxcloud = fields.Boolean(related="fiscal_position_id.is_taxcloud")
    total_tax_amount_tc = fields.Float("TaxCloud Total Tax")

    def _post(self, soft=True):
        # OVERRIDE

        # Don't change anything on moves used to cancel another ones.
        if self._context.get("move_reverse_cancel"):
            return super()._post(soft)

        refund_with_out_reverse = self.filtered(lambda move: move.fiscal_position_id.is_taxcloud and move.move_type == "out_refund" and not move.reversed_entry_id)
        if refund_with_out_reverse:
            raise UserError(
                _(
                    "This credit note cannot be posted because it isn't linked to an original invoice and the fiscal position uses TaxCloud.\n"
                    "Please cancel it and recreate the credit note from the original invoice."
                )
            )

        invoices_to_validate = self.filtered(
            lambda move: move.is_sale_document()
            and move.fiscal_position_id.is_taxcloud
            and not move._is_downpayment()
        )

        if invoices_to_validate:
            for invoice in invoices_to_validate.with_context(
                taxcloud_authorize_transaction=True
            ):
                invoice.validate_taxes_on_invoice()
        return super()._post(soft)

    def button_draft(self):
        """At confirmation below, the AuthorizedWithCapture encodes the invoice
        in TaxCloud. Returned cancels it for a refund.
        See https://dev.taxcloud.com/taxcloud/guides/5%20Returned%20Orders
        """
        if self.filtered(
            lambda inv: inv.move_type in ["out_invoice", "out_refund"]
            and inv.fiscal_position_id.is_taxcloud
        ):
            raise UserError(
                self.env._(
                    "You cannot cancel an invoice sent to TaxCloud.\n"
                    "You need to issue a refund (credit note) for it instead.\n"
                    "This way the tax entries will be cancelled in TaxCloud."
                )
            )
        return super().button_draft()

    @api.model
    def _get_TaxCloudRequest(self, api_id, api_key):
        return TaxCloudRequest(api_id, api_key)

    def get_taxcloud_reporting_date(self):
        if self.invoice_date:
            return datetime.datetime.combine(
                self.invoice_date, datetime.datetime.min.time()
            )
        else:
            return fields.Datetime.context_timestamp(self, datetime.datetime.now())

    # Used to prepare the taxcloud request
    # So that we can inherit this method in another modules to update the request.
    def prepare_taxcloud_request(self):
        shipper = self.company_id or self.env.company
        api_id = shipper.taxcloud_api_id
        api_key = shipper.taxcloud_api_key
        request = self._get_TaxCloudRequest(api_id, api_key)
        request.set_location_origin_detail(shipper)
        request.set_location_destination_detail(self.partner_shipping_id)
        request.set_invoice_items_detail(self)
        return request

    def validate_taxes_on_invoice(self):
        self.ensure_one()
        company = self.company_id
        request = self.prepare_taxcloud_request()
        if len(request.cart_items.CartItem) == 0 and len(self.invoice_line_ids.filtered(lambda x: x.display_type not in ("line_note", "line_section"))) \
            and self.env.company.is_skip_zero_invoice:
                return True
        if float_compare(self.amount_total, 0.0, precision_rounding=self.currency_id.rounding) < 0:
            raise UserError(self.env._(
                "You cannot validate a TaxCloud invoice with a negative total amount. "
                "You should create a credit note instead. "
                "Use the action menu to transform it into a credit note or refund."
            ))
        response = request.get_all_taxes_values()

        if response.get("error_message"):
            raise ValidationError(
                self.env._("Unable to retrieve taxes from TaxCloud: ")
                + "\n"
                + response["error_message"]
            )

        tax_values = response["values"]
        if tax_values:
            self.total_tax_amount_tc = sum([value for key, value in tax_values.items()])

        # warning: this is tightly coupled to TaxCloudRequest's _process_lines method
        # do not modify without syncing the other method
        raise_warning = False
        taxes_to_set = []
        tax_value_index = 0
        for index, line in enumerate(self.invoice_line_ids):
            if line.display_type in ("line_note", "line_section"):
                taxes_to_set.append((index, 0))
                continue

            if line._get_taxcloud_price() >= 0.0 and line.quantity >= 0.0:
                if not line.price_subtotal and self.env.company.is_skip_zero_invoice:
                    tax_value_index += 1
                    continue
                price = (
                    line.price_unit
                    * (1 - (line.discount or 0.0) / 100.0)
                    * line.quantity
                )
                if not price:
                    tax_rate = 0.0
                else:
                    if tax_value_index in tax_values:
                        tax_rate = tax_values[tax_value_index] / price * 100
                    else:
                        tax_rate = 0.0
                        _logger.warning(f"Tax value index {tax_value_index} not found in tax_values.")
                tax_value_index += 1
                if len(line.tax_ids) != 1 or float_compare(
                    line.tax_ids.amount, tax_rate, precision_digits=3
                ):
                    raise_warning = True
                    tax_rate = float_round(tax_rate, precision_digits=3)
                    tax = (
                        self.env["account.tax"]
                        .sudo()
                        .search(
                            [
                                *self.env["account.tax"]._check_company_domain(company),
                                ("amount", "=", tax_rate),
                                ("amount_type", "=", "percent"),
                                ("type_tax_use", "=", "sale"),
                            ],
                            limit=1,
                        )
                    )
                    if not tax:
                        # Only set if not already set, otherwise it triggers a
                        # needless and potentially heavy recompute for
                        # everything related to the tax.
                        # if not tax.active:
                            # Needs to be active to be included in invoice total computation
                            # tax.active = True
                    # else:
                        if company.is_default_tax_template:
                            values = company.tax_template_id.copy_data({
                                "name": "Tax %.3f %%" % (tax_rate),
                                "amount": tax_rate,
                                "invoice_label": "TaxCloud Tax",
                                "active": True
                            })
                        else:
                            values = {
                                        "name": "Tax %.3f %%" % (tax_rate),
                                        "amount": tax_rate,
                                        "amount_type": "percent",
                                        "type_tax_use": "sale",
                                        "description": "Sales Tax",
                                    }
                        tax = (
                                self.env["account.tax"]
                                .sudo()
                                .with_context(default_company_id=company.root_id.id)
                                .create(
                                    values
                                )
                            )
                    taxes_to_set.append((index, tax))

        for index, tax in taxes_to_set:
            line = self.invoice_line_ids[index]
            if line.display_type not in ("line_note", "line_section"):
                line.tax_ids = False
                line.tax_ids = tax

        if self.env.context.get("taxcloud_authorize_transaction"):
            reporting_date = self.get_taxcloud_reporting_date()

            if self.move_type == "out_invoice":
                response = request.get_taxcloud_authorize_with_capture(
                    self, reporting_date
                )
                if response.ResponseType == "Error":
                    raise ValidationError(response.Messages.ResponseMessage[0].Message)
            elif self.move_type == "out_refund":
                request.set_invoice_items_detail(self)
                origin_invoice = self.reversed_entry_id
                if origin_invoice:
                    response = request.get_taxcloud_returned(
                        origin_invoice, self.invoice_date
                    )
                    if response.ResponseType == "Error":
                        raise ValidationError(
                            response.Messages.ResponseMessage[0].Message
                        )
                else:
                    _logger.warning(
                        """The source document on the refund is not valid"""
                        """ and thus the refunded cart won't be logged on"""
                        """ your taxcloud account."""
                    )

        if raise_warning:
            return {
                "warning": self.env._(
                    """The tax rates have been updated, """
                    """ you may want to check it before validation"""
                )
            }
        else:
            return True

    def _invoice_paid_hook(self):
        for invoice in self:
            company = invoice.company_id
            if invoice.fiscal_position_id.is_taxcloud:
                api_id = company.taxcloud_api_id
                api_key = company.taxcloud_api_key
                request = TaxCloudRequest(api_id, api_key)
                if invoice.move_type == "out_invoice":
                    request.get_taxcloud_captured(invoice)
                else:
                    request.set_invoice_items_detail(invoice)
                    origin_invoice = invoice.reversed_entry_id
                    if origin_invoice:
                        request.get_taxcloud_returned(origin_invoice, invoice.invoice_date)
                    else:
                        _logger.warning(
                            """The source document on the refund %i is not valid"""
                            """ and thus the refunded cart won't be logged on your"""
                            """ taxcloud account""",
                            invoice.id,
                        )

        return super()._invoice_paid_hook()


class AccountMoveLine(models.Model):
    """Defines getters to have a common facade for order and move lines in TaxCloud."""

    _inherit = "account.move.line"

    def _get_taxcloud_price(self):
        self.ensure_one()
        return self.price_unit

    def _get_qty(self):
        self.ensure_one()
        return self.quantity
