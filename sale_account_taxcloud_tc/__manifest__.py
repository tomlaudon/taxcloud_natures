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
    "name": "Account TaxCloud - Sale",
    "summary": """This is the official Odoo TaxCloud integration supported by Taxcloud.
    This module computes the sales tax on the Sale Order using Tax Cloud API.
    """,
    "category": "Accounting/Accounting",
    "depends": ["account_taxcloud_tc", "sale", "sale_management"],
    "version": "1.0.4",
    "data": [
        "views/sale_order_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "auto_install": True,
    "license": "LGPL-3",
    "author": "Odoo S.A., Sodexis, TaxCloud",
    "website": "https://taxcloud.com/integrations/odoo/",
    "live_test_url": "https://sodexis.com/odoo-apps-store-demo",
    "images": ["images/main_screenshot.jpg"],
}
