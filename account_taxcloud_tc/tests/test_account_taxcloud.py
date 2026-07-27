from odoo.tests import tagged

from .common import TestAccountTaxcloudCommon


# post_install for the same reason as test_nw_deferred_capture: at_install runs
# before account_taxcloud_exemption_tc is in the registry, so the fixture
# partner misses the required tax_number_type default and setUpClass dies on the
# NOT NULL column. Without this the whole class is unrunnable on a real NW DB.
@tagged("post_install", "-at_install")
class TestAccountTaxcloud(TestAccountTaxcloudCommon):
    def test_01_taxcloud_tax_rate_on_invoice(self):
        """Test TaxRate Returned from taxcloud assign on invoice line"""
        # Create Invoice
        invoice = self.env["account.move"].create(
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
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_1.id,
                            "tax_ids": None,
                            "price_unit": self.product_1.list_price,
                        },
                    ),
                ],
            }
        )

        for line in invoice.invoice_line_ids:
            self.assertEqual(
                len(line.tax_ids), 0, "There should be no tax rate on the line."
            )
        with self.mock_taxcloud():
            invoice.action_post()

        for line in invoice.invoice_line_ids:
            self.assertEqual(
                len(line.tax_ids),
                1,
                "Taxcloud should have generated a unique tax rate for the line.",
            )
