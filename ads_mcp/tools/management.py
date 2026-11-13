# Copyright 2026 E Factor Leadership.
# Licensed under the Apache License, Version 2.0.
"""Additional guarded management operations. No operation commits by default."""

from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import propose_and_confirm as pac
from ads_mcp import utils

management_mcp = FastMCP("management")


def _id(value, name):
    digits = str(value).replace("-", "").strip()
    if not digits.isdigit() or not digits:
        raise ToolError(f"{name} must be a numeric ID.")
    return digits


def _text(value, name, max_length):
    s = str(value).strip()
    if not s or len(s) > max_length:
        raise ToolError(f"{name} must be 1 to {max_length} characters.")
    return s


def _money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ToolError("daily_amount must be a positive decimal.")
    if not amount.is_finite() or amount <= 0 or amount > 100000:
        raise ToolError("daily_amount must be between 0.01 and 100000.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ToolError("daily_amount allows two decimal places.")
    return int(amount * 1000000)


def _execute(customer_id, payload, change_items, request, service, method, validate_only, checksum):
    request.validate_only = True
    call = getattr(service, method)
    def _validate():
        request.validate_only = True
        call(request=request)
    def _commit():
        request.validate_only = False
        result = call(request=request)
        return "Google Ads mutation completed: " + ", ".join(x.resource_name for x in result.results)
    return pac.propose_and_confirm(
        customer_id=customer_id, validate_only=validate_only, checksum=checksum,
        operation_payload=payload, change_items=change_items,
        validate_fn=_validate, commit_fn=_commit,
        error_prefix="Google Ads management operation failed",
    )


@management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def create_daily_budget(customer_id: str, name: str, daily_amount: str,
                        login_customer_id: str | None = None,
                        validate_only: bool = True, checksum: str | None = None) -> str:
    """Create a dedicated average daily campaign budget (not a hard daily cap).
    DEFAULTS TO DRY RUN. Present proposal and secure explicit approval before commit."""
    cid = _id(customer_id, "customer_id")
    name = _text(name, "name", 255)
    micros = _money(daily_amount)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "CampaignBudgetService")
    operation = client.get_type("CampaignBudgetOperation")
    operation.create.name = name
    operation.create.amount_micros = micros
    operation.create.explicitly_shared = False
    request = client.get_type("MutateCampaignBudgetsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    payload = {"tool": "create_daily_budget", "name": name, "micros": micros, "shared": False}
    items = pac.build_change_items("CREATE", "Dedicated campaign budget", name,
        {"Daily average (account currency)": f"{Decimal(micros)/1000000:.2f}",
         "Shared": "No", "Spending warning": "Google may exceed average on individual days"})
    return _execute(cid, payload, items, request, service, "mutate_campaign_budgets", validate_only, checksum)


@management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def create_paused_search_campaign(customer_id: str, name: str, budget_id: str,
                                  eu_political_ads: bool = False,
                                  login_customer_id: str | None = None,
                                  validate_only: bool = True, checksum: str | None = None) -> str:
    """Create a PAUSED Google Search campaign with an existing budget and maximize-clicks bidding.
    Does not create ad groups, targeting, keywords, or ads. Cannot serve until completed and enabled.
    EU political advertising declaration is required; pass True only when accurate.
    DEFAULTS TO DRY RUN. Obtain explicit approval before commit."""
    cid = _id(customer_id, "customer_id")
    bid = _id(budget_id, "budget_id")
    name = _text(name, "name", 255)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "CampaignService")
    operation = client.get_type("CampaignOperation")
    campaign = operation.create
    campaign.name = name
    campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
    campaign.status = client.enums.CampaignStatusEnum.PAUSED
    campaign.campaign_budget = f"customers/{cid}/campaignBudgets/{bid}"
    campaign.target_spend = client.get_type("TargetSpend")
    campaign.network_settings.target_google_search = True
    campaign.network_settings.target_search_network = True
    campaign.network_settings.target_partner_search_network = False
    campaign.network_settings.target_content_network = False
    campaign.contains_eu_political_advertising = (
        client.enums.EuPoliticalAdvertisingStatusEnum.CONTAINS_EU_POLITICAL_ADVERTISING
        if eu_political_ads else
        client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    payload = {"tool": "create_paused_search_campaign", "name": name,
               "budget_id": bid, "eu_political_ads": eu_political_ads}
    items = pac.build_change_items("CREATE", "PAUSED Search campaign", name,
        {"Status": "PAUSED", "Budget ID": bid, "Bidding": "Maximize clicks",
         "Search partners": "Disabled", "Display expansion": "Disabled",
         "EU political ads": str(eu_political_ads)})
    return _execute(cid, payload, items, request, service, "mutate_campaigns", validate_only, checksum)


@management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def rename_campaign(customer_id: str, campaign_id: str, new_name: str,
                    login_customer_id: str | None = None,
                    validate_only: bool = True, checksum: str | None = None) -> str:
    """Rename existing campaign, with Google API validation and user confirmation."""
    cid = _id(customer_id, "customer_id")
    campid = _id(campaign_id, "campaign_id")
    new_name = _text(new_name, "new_name", 255)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "CampaignService")
    operation = client.get_type("CampaignOperation")
    rn = f"customers/{cid}/campaigns/{campid}"
    operation.update.resource_name = rn
    operation.update.name = new_name
    operation.update_mask.paths.append("name")
    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    payload = {"tool": "rename_campaign", "campaign_id": campid, "new_name": new_name}
    items = pac.build_change_items("UPDATE", "Campaign", rn, {"New name": new_name})
    return _execute(cid, payload, items, request, service, "mutate_campaigns", validate_only, checksum)


@management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def create_paused_ad_group(customer_id: str, campaign_id: str, name: str,
                           login_customer_id: str | None = None,
                           validate_only: bool = True, checksum: str | None = None) -> str:
    """Create a PAUSED standard Search ad group under an existing campaign."""
    cid = _id(customer_id, "customer_id")
    campid = _id(campaign_id, "campaign_id")
    name = _text(name, "name", 255)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "AdGroupService")
    operation = client.get_type("AdGroupOperation")
    operation.create.name = name
    operation.create.campaign = f"customers/{cid}/campaigns/{campid}"
    operation.create.status = client.enums.AdGroupStatusEnum.PAUSED
    operation.create.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    request = client.get_type("MutateAdGroupsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    payload = {"tool": "create_paused_ad_group", "campaign_id": campid, "name": name}
    items = pac.build_change_items("CREATE", "Ad group", name,
                                   {"Campaign ID": campid, "Status": "PAUSED"})
    return _execute(cid, payload, items, request, service, "mutate_ad_groups", validate_only, checksum)


@management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def create_paused_responsive_search_ad(customer_id: str, ad_group_id: str,
    headlines: list[str], descriptions: list[str], final_url: str,
    login_customer_id: str | None = None, validate_only: bool = True,
    checksum: str | None = None) -> str:
    """Create a PAUSED responsive Search ad (3-15 headlines, 2-4 descriptions).
    Google Ads may require additional account and campaign setup to approve it."""
    cid = _id(customer_id, "customer_id")
    gid = _id(ad_group_id, "ad_group_id")
    if not 3 <= len(headlines) <= 15 or not 2 <= len(descriptions) <= 4:
        raise ToolError("Provide 3-15 headlines and 2-4 descriptions.")
    headlines = [_text(t, "headline", 30) for t in headlines]
    descriptions = [_text(t, "description", 90) for t in descriptions]
    if len(set(headlines)) != len(headlines):
        raise ToolError("Headlines must be distinct.")
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ToolError("final_url must be an HTTPS address without embedded credentials.")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "AdGroupAdService")
    operation = client.get_type("AdGroupAdOperation")
    item = operation.create
    item.ad_group = f"customers/{cid}/adGroups/{gid}"
    item.status = client.enums.AdGroupAdStatusEnum.PAUSED
    item.ad.final_urls.append(final_url)
    for headline in headlines:
        asset = client.get_type("AdTextAsset")
        asset.text = headline
        item.ad.responsive_search_ad.headlines.append(asset)
    for desc in descriptions:
        asset = client.get_type("AdTextAsset")
        asset.text = desc
        item.ad.responsive_search_ad.descriptions.append(asset)
    request = client.get_type("MutateAdGroupAdsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    payload = {"tool": "create_paused_responsive_search_ad", "ad_group_id": gid,
               "headlines": headlines, "descriptions": descriptions, "final_url": final_url}
    items = pac.build_change_items("CREATE", "PAUSED Responsive Search Ad", f"Ad group {gid}",
        {"Status": "PAUSED", "Headlines": headlines, "Descriptions": descriptions,
         "Final URL": final_url})
    return _execute(cid, payload, items, request, service, "mutate_ad_group_ads", validate_only, checksum)


@management_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def set_ad_status(customer_id: str, ad_group_id: str, ad_id: str, status: str,
                  login_customer_id: str | None = None,
                  validate_only: bool = True, checksum: str | None = None) -> str:
    """PAUSE or ENABLE an existing ad; enabling may incur spend. Approval required."""
    cid = _id(customer_id, "customer_id")
    gid = _id(ad_group_id, "ad_group_id")
    aid = _id(ad_id, "ad_id")
    status = status.strip().upper()
    if status not in ("PAUSED", "ENABLED"):
        raise ToolError("status must be PAUSED or ENABLED.")
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "AdGroupAdService")
    operation = client.get_type("AdGroupAdOperation")
    rn = f"customers/{cid}/adGroupAds/{gid}~{aid}"
    operation.update.resource_name = rn
    operation.update.status = status
    operation.update_mask.paths.append("status")
    request = client.get_type("MutateAdGroupAdsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    payload = {"tool": "set_ad_status", "ad_group_id": gid, "ad_id": aid, "status": status}
    items = pac.build_change_items("UPDATE", "Ad", rn,
        {"Status": status, "Spend warning": "Enabling may incur advertising spend" if status == "ENABLED" else "Paused"})
    return _execute(cid, payload, items, request, service, "mutate_ad_group_ads", validate_only, checksum)
