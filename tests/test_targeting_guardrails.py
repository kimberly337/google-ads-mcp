"""Offline safety checks for campaign targeting; no customer mutation requests."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from ads_mcp import propose_and_confirm as pac
from ads_mcp.tools.targeting import (
    CENTRAL_OREGON_LOCATION_IDS,
    _digits,
    _money_micros,
    _name,
    _require_paused_pcp_campaign,
    _verify_pcp_conversion_actions,
)


class TargetingGuardrailsTest(unittest.TestCase):
    def test_approved_central_oregon_locations(self):
        self.assertEqual(
            set(CENTRAL_OREGON_LOCATION_IDS),
            {"1024400", "1024548", "1024566", "1024545", "9196279"},
        )
        self.assertEqual(
            CENTRAL_OREGON_LOCATION_IDS["1024548"], "Redmond, Oregon"
        )

    def test_us_pcp_targeting_rejects_active_or_unrelated_campaigns(self):
        enum = SimpleNamespace(PAUSED="PAUSED", ENABLED="ENABLED")
        client = SimpleNamespace(enums=SimpleNamespace(CampaignStatusEnum=enum))
        for name, status, should_pass in [
            ("E Factor | Performance Conversation Prep | USA", "PAUSED", True),
            (
                "E Factor | Performance Conversation Prep | USA",
                "ENABLED",
                False,
            ),
            ("E Factor | HR Consulting | Central Oregon", "PAUSED", False),
        ]:
            service = SimpleNamespace(
                search=lambda **kwargs: [
                    SimpleNamespace(
                        campaign=SimpleNamespace(name=name, status=status)
                    )
                ]
            )
            with patch(
                "ads_mcp.tools.targeting.utils.get_googleads_service_from_client",
                return_value=service,
            ):
                if should_pass:
                    _require_paused_pcp_campaign(client, "1212852768", "123456")
                else:
                    with self.assertRaises(ToolError):
                        _require_paused_pcp_campaign(
                            client, "1212852768", "123456"
                        )

    def test_us_pcp_targeting_fails_closed_with_missing_campaign(self):
        client = SimpleNamespace()
        service = SimpleNamespace(search=lambda **kwargs: [])
        with patch(
            "ads_mcp.tools.targeting.utils.get_googleads_service_from_client",
            return_value=service,
        ):
            with self.assertRaises(ToolError):
                _require_paused_pcp_campaign(client, "1212852768", "123456")

    def test_pcp_goal_action_validation(self):
        enums = SimpleNamespace(
            ConversionActionCategoryEnum=SimpleNamespace(
                SIGNUP="SIGNUP", BOOK_APPOINTMENT="BOOK_APPOINTMENT"
            ),
            ConversionOriginEnum=SimpleNamespace(WEBSITE="WEBSITE"),
            ConversionActionStatusEnum=SimpleNamespace(ENABLED="ENABLED"),
        )
        client = SimpleNamespace(enums=enums)

        def make_rows(appointment_category="BOOK_APPOINTMENT", signup_status="ENABLED"):
            return [
                SimpleNamespace(
                    conversion_action=SimpleNamespace(
                        id=7903192727, category="SIGNUP", origin="WEBSITE",
                        status=signup_status
                    )
                ),
                SimpleNamespace(
                    conversion_action=SimpleNamespace(
                        id=7902224587, category=appointment_category,
                        origin="WEBSITE", status="ENABLED"
                    )
                ),
            ]

        for rows, should_pass in [
            (make_rows(), True),
            (make_rows(appointment_category="PURCHASE"), False),
            (make_rows(signup_status="REMOVED"), False),
            (make_rows()[:1], False),
        ]:
            service = SimpleNamespace(search=lambda **kwargs: rows)
            with patch(
                "ads_mcp.tools.targeting.utils.get_googleads_service_from_client",
                return_value=service,
            ):
                if should_pass:
                    _verify_pcp_conversion_actions(client, "1212852768")
                else:
                    with self.assertRaises(ToolError):
                        _verify_pcp_conversion_actions(client, "1212852768")

    def test_numeric_ids_reject_injection(self):
        for value in ("1/2", "1;DROP", "123abc", "", "  - "):
            with self.subTest(value=value):
                with self.assertRaises(ToolError):
                    _digits(value, "campaign_id")
        self.assertEqual(_digits("121-285-2768", "customer_id"), "1212852768")

    def test_money_limits(self):
        self.assertEqual(_money_micros("2.50"), 2500000)
        for invalid in ("0", "-1", "0.001", "NaN", "Infinity", "1001"):
            with self.subTest(value=invalid):
                with self.assertRaises(ToolError):
                    _money_micros(invalid)

    def test_text_validation(self):
        with self.assertRaises(ToolError):
            _name("", "keyword")
        with self.assertRaises(ToolError):
            _name("x" * 26, "sitelink", 25)

    def test_customer_and_payload_checksum_binding(self):
        payload = {"tool": "add_search_keywords", "keywords": ["hr consultant"]}
        checksum = pac.compute_checksum("1212852768", payload)
        self.assertIsNone(pac.verify_checksum("1212852768", payload, checksum))
        self.assertIsNotNone(pac.verify_checksum("1212852768", payload, None))
        self.assertIsNotNone(pac.verify_checksum("1212852768", payload, "bad"))
        self.assertIsNotNone(
            pac.verify_checksum("9876543210", payload, checksum)
        )
        self.assertIsNotNone(
            pac.verify_checksum(
                "1212852768", {**payload, "keywords": ["different"]}, checksum
            )
        )


if __name__ == "__main__":
    unittest.main()
