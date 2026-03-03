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

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round, ormcache

from .taxcloud_request import TaxCloudRequest
_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = "sale.order"

    # Used to determine whether or not to warn the user to configure TaxCloud
    is_taxcloud_configured = fields.Boolean(related="company_id.is_taxcloud_configured")
    # Technical field to determine whether to hide taxes in views or not
    is_taxcloud = fields.Boolean(related="fiscal_position_id.is_taxcloud")
    total_tax_amount_tc = fields.Float("TaxCloud Total Tax")

    def action_quotation_send(self):
        self.validate_taxes_on_sales_order()
        return super().action_quotation_send()

    def action_quotation_sent(self):
        for order in self:
            order.validate_taxes_on_sales_order()
        return super().action_quotation_sent()

    @api.model
    def _get_TaxCloudRequest(self, api_id, api_key):
        return TaxCloudRequest(api_id, api_key)

    @api.model
    @ormcache("request_hash")
    def _get_all_taxes_values(self, request, request_hash):
        return request.get_all_taxes_values()

    # Used to prepare the taxcloud request
    # So that we can inherit this method in another modules to update the request.
    def prepare_taxcloud_request(self):
        shipper = self.company_id or self.env.company
        api_id = shipper.taxcloud_api_id
        api_key = shipper.taxcloud_api_key
        request = self._get_TaxCloudRequest(api_id, api_key)
        request.set_location_origin_detail(shipper)
        request.set_location_destination_detail(self.partner_shipping_id)
        request.set_order_items_detail(self)
        return request

    def validate_taxes_on_sales_order(self):
        if not self.fiscal_position_id.is_taxcloud:
            return True
        company = self.company_id
        request = self.prepare_taxcloud_request()
        if len(request.cart_items.CartItem) == 0 and len(self.order_line.filtered(lambda x: x.display_type not in ("line_note", "line_section"))) \
            and self.env.company.is_skip_zero_orders:
            return True
        request.taxcloud_date = fields.Datetime.context_timestamp(
            self, datetime.datetime.now()
        )
        if len(request.cart_items.CartItem) == 0 and \
            'fsm_task_id' in self._context \
            and self._context.get('fsm_task_id'):
            return True
        response = self._get_all_taxes_values(request, request.hash)

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
        for index, line in enumerate(
            self.order_line.filtered(lambda x: not x.display_type)
        ):
            if line._get_taxcloud_price() >= 0.0 and line.product_uom_qty >= 0.0:
                if not line.price_subtotal and self.env.company.is_skip_zero_orders:
                    continue
                price = (
                    line.price_unit
                    * (1 - (line.discount or 0.0) / 100.0)
                    * line.product_uom_qty
                )
                if not price:
                    tax_rate = 0.0
                else:
                    if index in tax_values:
                        tax_rate = tax_values[index] / price * 100
                    else:
                        tax_rate = 0.0
                        _logger.warning(f"Tax value index {index} not found in tax_values.")
                if len(line.tax_id) != 1 or float_compare(
                    line.tax_id.amount, tax_rate, precision_digits=3
                ):
                    tax_rate = float_round(tax_rate, precision_digits=3)
                    tax = (
                        self.env["account.tax"]
                        # .with_context(active_test=False)
                        .sudo().search(
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
                            # Needs to be active to be included in order total computation
                    #         tax.active = True
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
                            .with_context(default_company_id=company.id)
                            .create(
                                values
                            )
                        )
                    line.tax_id = tax
        return True

    def add_option_to_order_with_taxcloud(self):
        self.ensure_one()
        # portal user call this method with sudo
        if self.fiscal_position_id.is_taxcloud and self._uid == SUPERUSER_ID:
            self.validate_taxes_on_sales_order()

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            order.validate_taxes_on_sales_order()
        return res

    def write(self, vals):
        res = super().write(vals)
        for order in self:
            if (
                order.is_taxcloud
                and order.state == "sale"
                and "partner_shipping_id" in vals
            ):
                raise UserError(
                    "You can't change the delivery address once \
the sale order is confirmed when using TaxCloud."
                    "\nIf you still need to change the delivery address, \
please reset the order to a quotation and then update the delivery address."
                )
        return res

    @api.onchange("partner_shipping_id")
    def _onchange_warning_partner_shipping_id(self):
        res = {}
        taxcloud_warning = (
            self.is_taxcloud
            and self.state == "sale"
            and self.partner_shipping_id
            and (self._origin.partner_shipping_id.id != self.partner_shipping_id.id)
        )
        if taxcloud_warning:
            res["warning"] = {
                "title": self.env._("TaxCloud Warning!"),
                "message": self.env._(
                    "You can't change the delivery address once \
the sale order is confirmed when using TaxCloud."
                    "\nIf you still need to change the delivery address,\
please reset the order to a quotation and then update the delivery address.",
                ),
            }
        return res


class SaleOrderLine(models.Model):
    """Defines getters to have a common facade for order and invoice lines in TaxCloud."""

    _inherit = "sale.order.line"

    def _get_taxcloud_price(self):
        self.ensure_one()
        return self.price_unit

    def _get_qty(self):
        self.ensure_one()
        return self.product_uom_qty

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for order in res.mapped('order_id').filtered(lambda x:x.state == 'sale' and x.is_taxcloud):
            order.validate_taxes_on_sales_order()
        return res

    def write(self, values):
        res = super().write(values)
        for record in self.filtered(lambda line: line.order_id.state == 'sale' and line.order_id.is_taxcloud):
            if 'product_uom_qty' in values or 'price_unit' in values or ('discount' in values and values.get('discount') != record.discount):
                record.order_id.validate_taxes_on_sales_order()
        return res
