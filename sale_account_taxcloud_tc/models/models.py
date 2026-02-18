# copyright 2024 Sodexis
# license OPL-1 (see license file for full copyright and licensing details).

from odoo import models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def write(self, vals):
        res = super().write(vals)
        if "partner_id" in vals:
            taxcloud_pickings = self.filtered(
                lambda x: x.picking_type_code == "outgoing"
                and x.sale_id
                and x.sale_id.state == "sale"
                and x.sale_id.is_taxcloud
            )
            if taxcloud_pickings:
                raise UserError(
                    "You can't change the delivery address once the sale "
                    "order is confirmed when using TaxCloud."
                    "\nIf you still need to change the delivery address, "
                    "please reset the order to a quotation and then update the delivery address.",
                )
        return res
