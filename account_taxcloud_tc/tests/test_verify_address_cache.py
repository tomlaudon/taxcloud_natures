"""VerifyAddress answers are reused for an unchanged address.

The HTTP layer (requests.post) is mocked; no call leaves the machine.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from odoo.tests.common import TransactionCase, tagged

from ..models import taxcloud_request as tc
from ..models.taxcloud_request import TaxCloudRequest

POST = "odoo.addons.account_taxcloud_tc.models.taxcloud_request.requests.post"


def _partner(street="1 Main St", city="Middlefield", state="OH", zip_code="44062"):
    return SimpleNamespace(id=1, street=street, street2="", city=city,
                           state_id=SimpleNamespace(code=state), zip=zip_code)


def _answer(err=0, zip4="1234"):
    response = MagicMock()
    response.json.return_value = {
        "Address1": "1 MAIN ST", "Address2": "", "City": "MIDDLEFIELD",
        "State": "OH", "Zip5": "44062", "Zip4": zip4,
        "ErrNumber": str(err), "ErrDescription": "" if not err else "Bad address"}
    return response


@tagged('post_install', '-at_install')
class TestVerifyAddressCache(TransactionCase):

    def setUp(self):
        super().setUp()
        tc._VERIFY_CACHE.clear()
        self.addCleanup(tc._VERIFY_CACHE.clear)
        self.company = _partner(street="250 Executive Park Blvd", city="Millersburg")
        self.customer = _partner()

    def _tax_run(self):
        """What every tax run does: origin + destination."""
        request = TaxCloudRequest("api-id", "api-key")
        request.set_location_origin_detail(self.company)
        request.set_location_destination_detail(self.customer)
        return request

    def test_second_run_on_same_addresses_makes_no_call(self):
        with patch(POST, return_value=_answer()) as post:
            first = self._tax_run()
            self.assertEqual(post.call_count, 2)
            second = self._tax_run()
            self.assertEqual(post.call_count, 2)
        self.assertEqual(first.destination.Zip4, "1234")
        self.assertEqual(second.destination.Zip4, first.destination.Zip4)
        self.assertEqual(second.destination.Address1, first.destination.Address1)

    def test_any_changed_field_is_verified_again(self):
        with patch(POST, return_value=_answer()) as post:
            self._tax_run()
            calls = post.call_count
            for field, value in (("street", "2 Main St"), ("street2", "Apt 4"),
                                 ("city", "Burton"), ("zip", "44021")):
                setattr(self.customer, field, value)
                self._tax_run()
                calls += 1
                self.assertEqual(post.call_count, calls, field)
            self.customer.state_id = SimpleNamespace(code="PA")
            self._tax_run()
            self.assertEqual(post.call_count, calls + 1)

    def test_error_answers_and_timeouts_are_not_kept(self):
        with patch(POST, return_value=_answer(err=97)) as post:
            request = TaxCloudRequest("api-id", "api-key")
            request.set_location_destination_detail(self.customer)
            request.set_location_destination_detail(self.customer)
            self.assertEqual(post.call_count, 2)
        with patch(POST, side_effect=requests.exceptions.Timeout("down")) as post, \
                patch.object(tc.time, "sleep"):
            TaxCloudRequest("api-id", "api-key").set_location_destination_detail(self.customer)
            self.assertEqual(post.call_count, 3)
        # The next good answer is fetched, not an outage left behind.
        with patch(POST, return_value=_answer()) as post:
            request = TaxCloudRequest("api-id", "api-key")
            request.set_location_destination_detail(self.customer)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(request.destination.Zip4, "1234")

    def test_entries_expire(self):
        with patch(POST, return_value=_answer()) as post:
            request = TaxCloudRequest("api-id", "api-key")
            request.set_location_destination_detail(self.customer)
            key = next(iter(tc._VERIFY_CACHE))
            stored_at, answer = tc._VERIFY_CACHE[key]
            tc._VERIFY_CACHE[key] = (stored_at - tc.VERIFY_CACHE_TTL - 1, answer)
            request.set_location_destination_detail(self.customer)
            self.assertEqual(post.call_count, 2)

    def test_cache_size_is_bounded(self):
        with patch.object(tc, "VERIFY_CACHE_MAX", 3), patch(POST, return_value=_answer()):
            for number in range(5):
                self.customer.street = "%s Main St" % number
                TaxCloudRequest("api-id", "api-key").set_location_destination_detail(self.customer)
        self.assertEqual(len(tc._VERIFY_CACHE), 3)
