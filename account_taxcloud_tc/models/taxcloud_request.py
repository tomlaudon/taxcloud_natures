import hashlib
import logging
import re
import time

import requests

from odoo import fields, modules
from odoo.tools import LazyTranslate
from odoo.tools.zeep import Client
from odoo.tools.zeep.exceptions import Fault

_lt = LazyTranslate(__name__)

_logger = logging.getLogger(__name__)


class TaxCloudRequest:
    """Low-level object intended to interface Odoo recordsets with TaxCloud,
    through appropriate SOAP requests"""

    def __init__(self, api_id, api_key):
        wsdl_path = (
            modules.get_module_path("account_taxcloud_tc") + "/api/taxcloud.wsdl"
        )
        self.client = Client("file:///%s" % wsdl_path)
        self.factory = self.client.type_factory("ns0")
        self.api_login_id = api_id
        self.api_key = api_key
        self.ExemptionCertificate = None

    def verify_address(self, partner):
        # Ensure that the partner address is as
        # accurate as possible (with zip4 field for example)
        zip_match = re.match(r"^\D*(\d{5})\D*(\d{4})?", partner.zip or "")
        zips = list(zip_match.groups()) if zip_match else []
        address_to_verify = {
            "apiLoginID": self.api_login_id,
            "apiKey": self.api_key,
            "Address1": partner.street or "",
            "Address2": partner.street2 or "",
            "City": partner.city,
            "State": partner.state_id.code,
            "Zip5": zips.pop(0) if zips else "",
            "Zip4": zips.pop(0) if zips else "",
        }
        # A one-minute TaxCloud outage (2026-09-29 14:22, api.taxcloud.com
        # unreachable) used to surface as a failed customer payment: the
        # address check raised ConnectionError straight into the Auth wizard
        # (SO2654580). Address verification is an accuracy step, not the tax
        # lookup itself -- retry briefly, then continue with the unverified
        # address exactly as TaxCloud's own "cannot verify" answer does.
        res = None
        for attempt in range(3):
            try:
                res = requests.post(
                    "https://api.taxcloud.com/1.0/TaxCloud/VerifyAddress",
                    data=address_to_verify,
                    timeout=15,
                ).json()
                break
            except (requests.exceptions.RequestException, ValueError) as exc:
                _logger.warning(
                    "TaxCloud VerifyAddress attempt %s/3 failed for partner #%s: %s",
                    attempt + 1, partner.id, exc,
                )
                if attempt < 2:
                    time.sleep(1 + attempt)
        if res is None:
            _logger.warning(
                "TaxCloud VerifyAddress unreachable; using unverified address "
                "for partner #%s", partner.id,
            )
            return dict(address_to_verify)
        if int(res.get("ErrNumber", False)):
            # If VerifyAddress fails, use Lookup with the initial address
            _logger.info(
                "Could not verify address for partner #%s"
                " using taxcloud; using unverified address instead",
                partner.id,
            )
            res.update(address_to_verify)
        return res

    def set_location_origin_detail(self, shipper):
        address = self.verify_address(shipper)
        self.origin = self.factory.Address()
        self.origin.Address1 = address["Address1"] or ""
        self.origin.Address2 = address["Address2"] or ""
        self.origin.City = address["City"]
        self.origin.State = address["State"]
        self.origin.Zip5 = address["Zip5"]
        self.origin.Zip4 = address["Zip4"]

    def set_location_destination_detail(self, recipient_partner):
        address = self.verify_address(recipient_partner)
        self.destination = self.factory.Address()
        self.destination.Address1 = address["Address1"] or ""
        self.destination.Address2 = address["Address2"] or ""
        self.destination.City = address["City"]
        self.destination.State = address["State"]
        self.destination.Zip5 = address["Zip5"]
        self.destination.Zip4 = address["Zip4"]

    def set_items_detail(self, product_id, tic_code):
        self.cart_items = self.factory.ArrayOfCartItem()
        self.cart_item = self.factory.CartItem()
        self.cart_item.Index = 1
        self.cart_item.ItemID = product_id
        if tic_code:
            self.cart_item.TIC = tic_code
        # Send fixed price 100$ and Qty 1 to calculate percentage based on amount returned.
        self.cart_item.Price = 100
        self.cart_item.Qty = 1
        self.cart_items.CartItem = [self.cart_item]

    def set_invoice_items_detail(self, invoice):
        self.customer_id = invoice.partner_id.id
        self.taxcloud_date = invoice.get_taxcloud_reporting_date()
        self.cart_id = invoice.id
        self.cart_items = self.factory.ArrayOfCartItem()
        self.cart_items.CartItem = self._process_lines(invoice.invoice_line_ids)

    def _process_lines(self, lines):
        cart_items = []
        for index, line in enumerate(
            lines.filtered(
                lambda x: x.display_type not in ("line_note", "line_section")
            )
        ):
            qty = line._get_qty()
            if line._get_taxcloud_price() >= 0.0 and qty >= 0.0:
                skip_zero_orders = False
                if 'is_skip_zero_orders' in line.env.company._fields:
                    skip_zero_orders = line.env.company.is_skip_zero_orders
                skip_zero_invoice = line.env.company.is_skip_zero_invoice
                if not line.price_subtotal and (((skip_zero_orders) and line._name == 'sale.order.line') \
                    or ((skip_zero_invoice) and line._name == 'account.move.line')):
                    continue
                product_id = line.product_id.id
                tic_category = (
                    line.product_id.tic_category_id
                    or line.product_id.categ_id.tic_category_id
                    or line.company_id.tic_category_id
                    or line.env.company.tic_category_id
                )
                tic_code = None
                if tic_category:
                    tic_code = tic_category.code
                price_unit = line._get_taxcloud_price() * (
                    1 - (line.discount or 0.0) / 100.0
                )

                cart_item = self.factory.CartItem()
                cart_item.Index = index
                cart_item.ItemID = product_id
                cart_item.TIC = tic_code
                cart_item.Price = price_unit
                cart_item.Qty = qty
                cart_items.append(cart_item)
        return cart_items

    def get_all_taxes_values(self):
        customer_id = (
            hasattr(self, "customer_id") and self.customer_id or "NoCustomerID"
        )
        cart_id = hasattr(self, "cart_id") and self.cart_id or "NoCartID"
        _logger.info(
            "fetching tax values for cart %s (customer: %s)", cart_id, customer_id
        )
        formatted_response = {}
        if not self.api_login_id or not self.api_key:
            formatted_response["error_message"] = _lt(
                "Please configure taxcloud credentials on the current company "
                "or use a different fiscal position"
            )
            return formatted_response
        if len(self.cart_items.CartItem) == 0:
            formatted_response["error_message"] = "No Cart Items"
            return formatted_response

        try:
            response = self.client.service.LookupForDate(
                self.api_login_id,
                self.api_key,
                customer_id,
                cart_id,
                self.cart_items,
                self.origin,
                self.destination,
                False,  # deliveredBySeller
                self.ExemptionCertificate,  # exemptCert
                self.taxcloud_date,  # useDate
            )
            formatted_response["response"] = response
            if response.ResponseType == "OK":
                formatted_response["values"] = {}
                if response.CartItemsResponse and response.CartItemsResponse.CartItemResponse:
                    for item in response.CartItemsResponse.CartItemResponse:
                        index = item.CartItemIndex
                        tax_amount = item.TaxAmount
                        formatted_response["values"][index] = tax_amount
            elif response.ResponseType == "Error":
                formatted_response["error_message"] = response.Messages.ResponseMessage[
                    0
                ].Message
        except Fault as fault:
            formatted_response["error_message"] = fault.message
        except OSError:
            formatted_response["error_message"] = "TaxCloud Server Not Found"
        return formatted_response

    # Get TIC category on synchronize.
    def get_tic_category(self):
        formatted_response = {}
        try:
            self.response = self.client.service.GetTICs(self.api_login_id, self.api_key)
            if self.response.ResponseType == "OK":
                formatted_response["data"] = self.response.TICs.TIC
            elif self.response.ResponseType == "Error":
                formatted_response[
                    "error_message"
                ] = self.response.Messages.ResponseMessage[0].Message
        except Fault as fault:
            formatted_response["error_message"] = fault.message
        except OSError:
            formatted_response["error_message"] = "TaxCloud Server Not Found"

        return formatted_response

    def get_taxcloud_authorize_with_capture(self, invoice_id, reporting_date):
        return self.client.service.AuthorizedWithCapture(
            self.api_login_id,
            self.api_key,
            self.customer_id,
            self.cart_id,
            invoice_id.id,
            reporting_date,  # DateAuthorized
            reporting_date,  # DateCaptured
        )

    def get_taxcloud_returned(self, origin_invoice, invoice_date):
        return self.client.service.Returned(
            self.api_login_id,
            self.api_key,
            origin_invoice.id,
            self.cart_items,
            fields.Datetime.from_string(invoice_date),
        )

    def get_taxcloud_captured(self, invoice):
        return self.client.service.Captured(
            self.api_login_id,
            self.api_key,
            invoice.id,
        )

    @property
    def hash(self):
        # The hash is used as key to cache request responses,
        # to avoid using too much space in the cache.
        # The current date is appended to refresh the value every day.
        hash_parameters = (
            (self.api_login_id or "")
            + (self.api_key or "")
            + str(hasattr(self, "customer_id") and self.customer_id or "NoCustomerID")
            + str(hasattr(self, "cart_id") and self.cart_id or "NoCartID")
            + str(self.cart_items)
            + str(self.origin)
            + str(self.destination)
            + fields.Date.to_string(fields.Date.today())
        )
        if hasattr(self, "ExemptionCertificate") and hasattr(
            self.ExemptionCertificate, "CertificateID"
        ):
            hash_parameters += str(self.ExemptionCertificate.CertificateID)
        return hashlib.sha1((hash_parameters).encode("utf-8")).hexdigest()
