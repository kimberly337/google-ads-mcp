"""Offline guardrail tests: never connect to a live Google Ads account."""

import unittest
from fastmcp.exceptions import ToolError
from ads_mcp import propose_and_confirm as pac
from ads_mcp.tools.management import _id, _money, _text


class Guardrails(unittest.TestCase):
    def test_id_rejects_non_numeric(self):
        with self.assertRaises(ToolError):
            _id("1;DROP", "customer_id")

    def test_currency_precision_and_range(self):
        for invalid in ["0", "-2", "2.123", "NaN", "Infinity", "100001"]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ToolError):
                    _money(invalid)
        self.assertEqual(_money("10.50"), 10500000)

    def test_empty_campaign_name_rejected(self):
        with self.assertRaises(ToolError):
            _text("", "name", 255)

    def test_confirmation_requires_checksum(self):
        payload = {"operation": "update", "status": "PAUSED"}
        self.assertIsNotNone(pac.verify_checksum("1212852768", payload, None))
        self.assertIsNotNone(pac.verify_checksum("1212852768", payload, "bad"))
        good = pac.compute_checksum("1212852768", payload)
        self.assertIsNone(pac.verify_checksum("1212852768", payload, good))
        self.assertIsNotNone(pac.verify_checksum("9999999999", payload, good))


if __name__ == "__main__":
    unittest.main()
