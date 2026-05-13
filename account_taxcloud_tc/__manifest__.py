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

{
    "name": "Account TaxCloud",
    "summary": """This is the official Odoo TaxCloud integration supported by Taxcloud.
      This module computes the sales tax on the Invoice using Tax Cloud API.
    """,
    "category": "Accounting/Accounting",
    "version": "1.0.5",
    "depends": ["account", "l10n_us"],
    "data": [
        "security/ir.model.access.csv",
        "views/account_fiscal_position_view.xml",
        "views/product_view.xml",
        "views/res_config_settings_views.xml",
        "views/account_invoice_views.xml",
        "data/account_taxcloud_tc_data.xml",
        "data/mail_template_data.xml",
    ],
    "license": "LGPL-3",
    "author": "Odoo S.A., Sodexis, TaxCloud",
    "website": "https://taxcloud.com/integrations/odoo/",
    "live_test_url": "https://sodexis.com/odoo-apps-store-demo",
    "pre_init_hook": "pre_init_hook",
    "images": ["images/main_screenshot.jpg"],
}
