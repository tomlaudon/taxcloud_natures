from unittest.mock import Mock, patch

from odoo.tests import tagged

from .common import TestAccountTaxcloudCommon

_CAPTURE_PATH = (
    "odoo.addons.account_taxcloud_tc.models.taxcloud_request."
    "TaxCloudRequest.get_taxcloud_authorize_with_capture"
)


# post_install: at_install runs before account_taxcloud_exemption_tc is in the
# registry, so res_partner.tax_number_type (required, default TaxID, added by
# that module) is absent from the create and the NOT NULL column rejects the
# fixture partner. Nothing to do with the code under test -- it just makes this
# suite runnable on a real NW database.
@tagged("post_install", "-at_install")
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

    @staticmethod
    def _error_response(message):
        """A TaxCloud Error response shaped like the real SOAP object."""
        return Mock(
            ResponseType="Error",
            Messages=Mock(ResponseMessage=[Mock(Message=message)]),
        )

    def _post_pending_invoice(self):
        """A posted invoice already flagged for the deferred-capture cron."""
        self.env["ir.config_parameter"].sudo().set_param(
            "account_taxcloud_tc.defer_capture", "True"
        )
        invoice = self._make_invoice()
        with self.mock_taxcloud(), patch(_CAPTURE_PATH) as capture_mock:
            capture_mock.return_value = Mock(ResponseType="Success")
            invoice.action_post()
        self.assertTrue(invoice.nw_taxcloud_capture_pending)
        return invoice

    def test_already_authorized_response_clears_the_pending_flag(self):
        """TaxCloud answering "already been marked as authorized" means the tax
        IS captured — treating it as a failure is what made INV334415 retry
        every ~3 minutes forever."""
        invoice = self._post_pending_invoice()
        response = self._error_response(
            "This transaction has already been marked as authorized "
            "(CartID : %s)" % invoice.id
        )
        with self.mock_taxcloud(), patch(_CAPTURE_PATH) as capture_mock:
            capture_mock.return_value = response
            self.env["account.move"]._cron_taxcloud_capture()
            # Short-circuits: no wasted second capture / re-Lookup round trip.
            self.assertEqual(capture_mock.call_count, 1)

        self.assertFalse(
            invoice.nw_taxcloud_capture_pending,
            "An already-captured invoice must stop being retried.",
        )
        self.assertTrue(
            invoice.taxcloud_is_authorized,
            "It is authorized at TaxCloud, so record that.",
        )

    def test_genuine_error_still_retries_next_run(self):
        """Real failures must keep the old behaviour: re-Lookup, retry once,
        then raise and stay pending."""
        invoice = self._post_pending_invoice()
        with self.mock_taxcloud(), patch(_CAPTURE_PATH) as capture_mock:
            capture_mock.return_value = self._error_response(
                "Invalid API credentials"
            )
            self.env["account.move"]._cron_taxcloud_capture()
            self.assertEqual(
                capture_mock.call_count, 2, "should re-Lookup and retry once"
            )

        self.assertTrue(
            invoice.nw_taxcloud_capture_pending,
            "A genuine failure must stay pending for the next sweep.",
        )
        self.assertFalse(invoice.taxcloud_is_authorized)

    def test_already_authorized_on_the_retry_is_also_success(self):
        """First call fails for an unrelated reason (aged-out cart), the retry
        after the fresh Lookup comes back already-authorized."""
        invoice = self._post_pending_invoice()
        responses = [
            self._error_response("CartID not found"),
            self._error_response(
                "This transaction has already been marked as authorized "
                "(CartID : 999)"
            ),
        ]
        with self.mock_taxcloud(), patch(_CAPTURE_PATH) as capture_mock:
            capture_mock.side_effect = responses
            self.env["account.move"]._cron_taxcloud_capture()
            self.assertEqual(capture_mock.call_count, 2)

        self.assertFalse(invoice.nw_taxcloud_capture_pending)
        self.assertTrue(invoice.taxcloud_is_authorized)

    def test_malformed_error_response_does_not_mask_the_failure(self):
        """A response with no usable Messages used to raise AttributeError from
        inside the error path; it must still be a clean stay-pending failure."""
        invoice = self._post_pending_invoice()
        with self.mock_taxcloud(), patch(_CAPTURE_PATH) as capture_mock:
            capture_mock.return_value = Mock(ResponseType="Error", Messages=None)
            self.env["account.move"]._cron_taxcloud_capture()

        self.assertTrue(invoice.nw_taxcloud_capture_pending)
        self.assertFalse(invoice.taxcloud_is_authorized)

    def test_already_captured_matcher_is_narrow(self):
        """The matcher must not swallow unrelated errors."""
        Move = self.env["account.move"]
        for message, expected in [
            ("This transaction has already been marked as authorized", True),
            ("Transaction has already been marked as captured (CartID : 1)", True),
            ("Invalid API credentials", False),
            ("Cart not found", False),
            ("The transaction has not been marked as authorized", False),
            ("", False),
        ]:
            self.assertEqual(
                Move._nw_taxcloud_already_captured(self._error_response(message)),
                expected,
                "matcher verdict on %r" % message,
            )
        # No message at all -> not a success, and no exception extracting it.
        self.assertFalse(
            Move._nw_taxcloud_already_captured(
                Mock(ResponseType="Error", Messages=None)
            )
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
