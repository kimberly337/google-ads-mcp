# Copyright 2026 E Factor Leadership.
# Licensed under the Apache License, Version 2.0.
"""Guarded setup tools for Search campaigns.

All operations default to validate_only=True. Approval is required before
calling again with validate_only=False and a matching checksum.
"""
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import propose_and_confirm as pac, utils

targeting_mcp = FastMCP("targeting")

CENTRAL_OREGON_LOCATION_IDS = {
    "1024400": "Bend, Oregon",
    "1024548": "Redmond, Oregon",
    "1024566": "Sisters, Oregon",
    "1024545": "Prineville, Oregon",
    "9196279": "Sunriver, Oregon",
}


def _digits(value, label):
    normalized = str(value).replace("-", "").strip()
    if not normalized.isdigit():
        raise ToolError(f"{label} must contain only numeric digits.")
    return normalized


def _name(value, label, max_length=80):
    normalized = str(value).strip()
    if not normalized or len(normalized) > max_length:
        raise ToolError(f"{label} must contain between 1 and {max_length} characters.")
    return normalized


def _money_micros(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ToolError("CPC ceiling must be a valid decimal amount.")
    if not amount.is_finite() or amount < Decimal("0.01") or amount > Decimal("1000"):
        raise ToolError("CPC ceiling must be between 0.01 and 1000 account-currency units.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ToolError("CPC ceiling may have at most two decimal places.")
    return int(amount * 1000000)


def _prepare(customer_id, operations, request_type, service_type,
             method, login_customer_id):
    cid = _digits(customer_id, "customer_id")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, service_type)
    request = client.get_type(request_type)
    request.customer_id = cid
    request.operations.extend(operations(client, cid))
    request.partial_failure = False
    return cid, service, request, method


def _propose(cid, service, request, method, payload, items, validate_only, checksum):
    call = getattr(service, method)

    def validate():
        request.validate_only = True
        call(request=request)

    def commit():
        request.validate_only = False
        result = call(request=request)
        return "Google Ads operation completed: " + ", ".join(
            item.resource_name for item in result.results
        )

    return pac.propose_and_confirm(
        customer_id=cid, validate_only=validate_only, checksum=checksum,
        operation_payload=payload, change_items=items,
        validate_fn=validate, commit_fn=commit,
        error_prefix="Google Ads targeting update failed",
    )


@targeting_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def add_central_oregon_locations(
    customer_id: str, campaign_id: str,
    location_ids: list[str],
    login_customer_id: str | None = None,
    validate_only: bool = True, checksum: str | None = None,
) -> str:
    """Add selected, verified Central Oregon cities to an existing PAUSED campaign.
    This tool is restricted to Bend, Redmond OR, Sisters, Prineville, and Sunriver.
    It does not change geographic Presence settings. Default is dry run.
    """
    camp = _digits(campaign_id, "campaign_id")
    ids = [_digits(x, "location_id") for x in location_ids]
    if not ids or len(ids) > 5 or len(set(ids)) != len(ids):
        raise ToolError("Provide between 1 and 5 distinct Google location IDs.")
    if any(i not in CENTRAL_OREGON_LOCATION_IDS for i in ids):
        raise ToolError("Only the five approved Central Oregon city IDs are accepted.")

    def ops(client, cid):
        result = []
        for geo in ids:
            op = client.get_type("CampaignCriterionOperation")
            criterion = op.create
            criterion.campaign = f"customers/{cid}/campaigns/{camp}"
            criterion.location.geo_target_constant = f"geoTargetConstants/{geo}"
            result.append(op)
        return result

    cid, service, request, method = _prepare(
        customer_id, ops, "MutateCampaignCriteriaRequest",
        "CampaignCriterionService", "mutate_campaign_criteria", login_customer_id
    )
    payload = {"tool": "add_central_oregon_locations", "campaign_id": camp, "location_ids": ids}
    items = pac.build_change_items(
        "CREATE", "Campaign location targeting", f"Campaign {camp}",
        {"Included locations": [CENTRAL_OREGON_LOCATION_IDS[i] for i in ids]}
    )
    return _propose(cid, service, request, method, payload, items, validate_only, checksum)


@targeting_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def set_presence_and_max_cpc(
    customer_id: str, campaign_id: str, cpc_ceiling: str,
    login_customer_id: str | None = None,
    validate_only: bool = True, checksum: str | None = None,
) -> str:
    """Configure location-positive PRESENCE (not interest) and a CPC ceiling for
    an existing Maximize Clicks/TargetSpend campaign. Do not activate campaigns.
    CPC ceiling is a bid limit, NOT a total campaign spend limit.
    """
    camp = _digits(campaign_id, "campaign_id")
    micros = _money_micros(cpc_ceiling)

    def ops(client, cid):
        op = client.get_type("CampaignOperation")
        campaign = op.update
        campaign.resource_name = f"customers/{cid}/campaigns/{camp}"
        campaign.geo_target_type_setting.positive_geo_target_type = (
            client.enums.PositiveGeoTargetTypeEnum.PRESENCE
        )
        campaign.target_spend.cpc_bid_ceiling_micros = micros
        op.update_mask.paths.extend([
            "geo_target_type_setting.positive_geo_target_type",
            "target_spend.cpc_bid_ceiling_micros",
        ])
        return [op]

    cid, service, request, method = _prepare(
        customer_id, ops, "MutateCampaignsRequest",
        "CampaignService", "mutate_campaigns", login_customer_id
    )
    payload = {"tool": "set_presence_and_max_cpc", "campaign_id": camp, "cpc_micros": micros}
    items = pac.build_change_items(
        "UPDATE", "Campaign settings", f"Campaign {camp}",
        {"Positive location target": "PRESENCE",
         "Maximum CPC bid (account currency)": f"{Decimal(micros)/1000000:.2f}"}
    )
    return _propose(cid, service, request, method, payload, items, validate_only, checksum)


@targeting_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def add_search_keywords(
    customer_id: str, ad_group_id: str,
    keywords: list[str], match_type: str,
    login_customer_id: str | None = None,
    validate_only: bool = True, checksum: str | None = None,
) -> str:
    """Add up to 25 positive exact- or phrase-match keywords to an existing ad group.
    Specify match_type EXACT or PHRASE; do not include bracket/quote notation.
    Validation-only by default.
    """
    gid = _digits(ad_group_id, "ad_group_id")
    match = str(match_type).strip().upper()
    if match not in ("EXACT", "PHRASE"):
        raise ToolError("Only EXACT or PHRASE keyword matches are supported.")
    words = [_name(k, "keyword") for k in keywords]
    if not words or len(words) > 25 or len(set(k.lower() for k in words)) != len(words):
        raise ToolError("Provide 1-25 unique keywords.")
    if any("[" in k or "]" in k or '"' in k for k in words):
        raise ToolError("Supply plain keywords, without Google match-type punctuation.")

    def ops(client, cid):
        result = []
        for word in words:
            op = client.get_type("AdGroupCriterionOperation")
            criterion = op.create
            criterion.ad_group = f"customers/{cid}/adGroups/{gid}"
            criterion.keyword.text = word
            criterion.keyword.match_type = getattr(client.enums.KeywordMatchTypeEnum, match)
            result.append(op)
        return result

    cid, service, request, method = _prepare(
        customer_id, ops, "MutateAdGroupCriteriaRequest",
        "AdGroupCriterionService", "mutate_ad_group_criteria", login_customer_id
    )
    payload = {"tool": "add_search_keywords", "ad_group_id": gid, "match_type": match, "keywords": words}
    items = pac.build_change_items(
        "CREATE", "Search keywords", f"Ad group {gid}",
        {"Match type": match, "Keywords": words}
    )
    return _propose(cid, service, request, method, payload, items, validate_only, checksum)


@targeting_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def add_campaign_negative_keywords(
    customer_id: str, campaign_id: str,
    keywords: list[str], match_type: str = "PHRASE",
    login_customer_id: str | None = None,
    validate_only: bool = True, checksum: str | None = None,
) -> str:
    """Add 1-25 campaign-level negative phrase/exact keywords.
    Match types accepted: PHRASE and EXACT. Defaults to dry run.
    """
    camp = _digits(campaign_id, "campaign_id")
    match = str(match_type).strip().upper()
    if match not in ("EXACT", "PHRASE"):
        raise ToolError("Negative match_type must be EXACT or PHRASE.")
    words = [_name(k, "negative keyword") for k in keywords]
    if not words or len(words) > 25 or len(set(k.lower() for k in words)) != len(words):
        raise ToolError("Provide 1-25 unique negative keywords.")

    def ops(client, cid):
        result = []
        for word in words:
            op = client.get_type("CampaignCriterionOperation")
            criterion = op.create
            criterion.campaign = f"customers/{cid}/campaigns/{camp}"
            criterion.negative = True
            criterion.keyword.text = word
            criterion.keyword.match_type = getattr(client.enums.KeywordMatchTypeEnum, match)
            result.append(op)
        return result

    cid, service, request, method = _prepare(
        customer_id, ops, "MutateCampaignCriteriaRequest",
        "CampaignCriterionService", "mutate_campaign_criteria", login_customer_id
    )
    payload = {"tool": "add_campaign_negative_keywords", "campaign_id": camp, "match_type": match, "keywords": words}
    items = pac.build_change_items(
        "CREATE", "Campaign negative keywords", f"Campaign {camp}",
        {"Match type": match, "Excluded keywords": words}
    )
    return _propose(cid, service, request, method, payload, items, validate_only, checksum)


@targeting_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def set_campaign_conversion_goal(
    customer_id: str, campaign_id: str,
    category: str, origin: str, biddable: bool,
    login_customer_id: str | None = None,
    validate_only: bool = True, checksum: str | None = None,
) -> str:
    """Set biddability for an existing campaign conversion goal category+origin.

    Booked consulting appointments: category BOOK_APPOINTMENT, origin GOOGLE_ANALYTICS.
    This affects ALL conversion actions with the same category and origin in the
    campaign. Does not change account-wide conversion settings.
    """
    camp = _digits(campaign_id, "campaign_id")
    cat = str(category).strip().upper()
    org = str(origin).strip().upper()
    # Restrict changes to expected campaign conversion categories.
    if cat not in ("BOOK_APPOINTMENT", "SUBMIT_LEAD_FORM", "SIGNUP", "PURCHASE"):
        raise ToolError("Unsupported category. Use an explicitly reviewed lead or purchase goal.")
    if org not in ("GOOGLE_ANALYTICS", "WEBSITE"):
        raise ToolError("Unsupported origin. Use GOOGLE_ANALYTICS or WEBSITE.")

    def ops(client, cid):
        op = client.get_type("CampaignConversionGoalOperation")
        target = op.update
        target.resource_name = (
            f"customers/{cid}/campaignConversionGoals/{camp}~{cat}~{org}"
        )
        target.biddable = bool(biddable)
        op.update_mask.paths.append("biddable")
        return [op]

    cid, service, request, method = _prepare(
        customer_id, ops, "MutateCampaignConversionGoalsRequest",
        "CampaignConversionGoalService", "mutate_campaign_conversion_goals",
        login_customer_id,
    )
    payload = {"tool": "set_campaign_conversion_goal", "campaign_id": camp,
               "category": cat, "origin": org, "biddable": bool(biddable)}
    items = pac.build_change_items(
        "UPDATE", "Campaign conversion goal", f"Campaign {camp}",
        {"Conversion category": cat, "Origin": org,
         "Biddable": bool(biddable),
         "Scope": "All conversion actions in this category and origin"}
    )
    return _propose(cid, service, request, method, payload, items, validate_only, checksum)


@targeting_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def create_booking_sitelink(
    customer_id: str, link_text: str, final_url: str,
    description1: str, description2: str,
    login_customer_id: str | None = None,
    validate_only: bool = True, checksum: str | None = None,
) -> str:
    """Create an UNATTACHED sitelink asset, with two descriptions.
    The asset cannot appear in ads until explicitly linked to a campaign.
    """
    link = _name(link_text, "link_text", 25)
    desc1 = _name(description1, "description1", 35)
    desc2 = _name(description2, "description2", 35)
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ToolError("final_url must be a valid HTTPS URL without credentials.")

    def ops(client, cid):
        op = client.get_type("AssetOperation")
        asset = op.create
        asset.sitelink_asset.link_text = link
        asset.sitelink_asset.description1 = desc1
        asset.sitelink_asset.description2 = desc2
        asset.final_urls.append(final_url)
        return [op]

    cid, service, request, method = _prepare(
        customer_id, ops, "MutateAssetsRequest",
        "AssetService", "mutate_assets", login_customer_id,
    )
    payload = {"tool": "create_booking_sitelink", "link_text": link,
               "final_url": final_url, "description1": desc1, "description2": desc2}
    items = pac.build_change_items(
        "CREATE", "Unattached sitelink asset", link,
        {"Destination": final_url, "Description 1": desc1,
         "Description 2": desc2, "Serving status": "Not attached"}
    )
    return _propose(cid, service, request, method, payload, items, validate_only, checksum)


@targeting_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def attach_booking_sitelink(
    customer_id: str, campaign_id: str, asset_id: str,
    login_customer_id: str | None = None,
    validate_only: bool = True, checksum: str | None = None,
) -> str:
    """Link an existing sitelink asset to an existing campaign.
    This may make a sitelink eligible to serve if campaign is enabled.
    Confirm campaign's PAUSED status before committing.
    """
    camp = _digits(campaign_id, "campaign_id")
    aid = _digits(asset_id, "asset_id")

    def ops(client, cid):
        op = client.get_type("CampaignAssetOperation")
        link = op.create
        link.campaign = f"customers/{cid}/campaigns/{camp}"
        link.asset = f"customers/{cid}/assets/{aid}"
        link.field_type = client.enums.AssetFieldTypeEnum.SITELINK
        return [op]

    cid, service, request, method = _prepare(
        customer_id, ops, "MutateCampaignAssetsRequest",
        "CampaignAssetService", "mutate_campaign_assets", login_customer_id,
    )
    payload = {"tool": "attach_booking_sitelink", "campaign_id": camp, "asset_id": aid}
    items = pac.build_change_items(
        "CREATE", "Campaign sitelink association", f"Campaign {camp}",
        {"Sitelink asset ID": aid,
         "Serving warning": "Can show if campaign is enabled"}
    )
    return _propose(cid, service, request, method, payload, items, validate_only, checksum)
