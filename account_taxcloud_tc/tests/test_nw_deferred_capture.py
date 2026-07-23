from unittest.mock import Mock, patch

from .common import TestAccountTaxcloudCommon

_CAPTURE_PATH = (
    "odoo.addons.account_taxcloud_tc.models.taxcloud_request."
    "TaxCloudRequest.get_taxcloud_authorize_with_capture"
)


class TestNwDeferredCapture(TestAccountTaxcloudCommon):
    """NW: the TaxCloud AuthorizedWithCapture report is deferred off the
    synchronous post/barcode-Validate path to the _cron_taxcloud_capture cron,
    while the invoice tax is still computed + set inline at post."""

    def _make_invoice(self):
        return self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "fiscal_position_id": self.fiscal_position.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "tax_ids": None,
                            "price_unit": self.product.list_price,
                        },
                    ),
                ],
            }
        )

    def test_capture_deferred_at_post_then_sent_by_cron(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "account_taxcloud_tc.defer_capture", "True"
        )
        invoice = self._make_invoice()
        with self.mock_taxcloud(), patch(_CAPTURE_PATH) as capture_mock:
            capture_mock.return_value = Mock(ResponseType="Success")
            invoice.action_post()

            # Tax is still computed + applied inline at post (Lookup ran).
            taxable = invoice.invoice_line_ids.filtered(
                lambda l: l.display_type not in ("line_note", "line_section")
            )
            self.assertTrue(
                all(len(l.tax_ids) == 1 for l in taxable),
                "Invoice tax must still be set inline at post.",
            )
            # ...but the AuthorizedWithCapture report is NOT sent inline.
            self.assertEqual(
                capture_mock.call_count,
                0,
                "AuthorizedWithCapture must not run inline at post when deferred.",
            )
            self.assertTrue(
                invoice.nw_taxcloud_capture_pending,
                "Invoice should be flagged for the deferred capture cron.",
            )

            # The cron sends the deferred report and clears the flag.
            self.env["account.move"]._cron_taxcloud_capture()
            self.assertGreaterEqual(
                capture_mock.call_count,
                1,
                "The cron must send the deferred AuthorizedWithCapture.",
            )
        self.assertFalse(
            invoice.nw_taxcloud_capture_pending,
            "Pending flag should be cleared once the capture is sent.",
        )

    def test_capture_inline_when_kill_switch_off(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "account_taxcloud_tc.defer_capture", "False"
        )
        invoice = self._make_invoice()
        with self.mock_taxcloud(), patch(_CAPTURE_PATH) as capture_mock:
            capture_mock.return_value = Mock(ResponseType="Success")
            invoice.action_post()
            self.assertGreaterEqual(
                capture_mock.call_count,
                1,
                "With defer disabled, capture must run inline at post.",
            )
            self.assertFalse(
                invoice.nw_taxcloud_capture_pending,
                "No pending flag when capturing inline.",
            )
