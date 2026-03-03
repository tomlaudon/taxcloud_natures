# copyright 2024 Sodexis
# license OPL-1 (see license file for full copyright and licensing details).

from odoo import models
from odoo.exceptions import UserError


class Base(models.AbstractModel):
    _inherit = "base"

    def write(self, vals):
        res = super().write(vals)
        if (
            self.filtered(
                lambda x: x._name == "stock.picking"
                and x.picking_type_code == "outgoing"
                and x.sale_id
                and x.sale_id.state == "sale"
                and x.sale_id.is_taxcloud
            )
            and "partner_id" in vals
        ):
            raise UserError(
                "You can't change the delivery address once the sale \
order is confirmed when using TaxCloud."
                "\nIf you still need to change the delivery address,\
please reset the order to a quotation and then update the delivery address.",
            )
        return res
