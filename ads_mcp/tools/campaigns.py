# Copyright 2026 E Factor Leadership. Apache-2.0.
"""Conservative campaign and budget management with mandatory propose/confirm.

Write access is disabled until GOOGLE_ADS_MCP_WRITE_CUSTOMER_ID is set.
Every create starts PAUSED. Every change is API-validated without executing it
before a checksum-backed confirmation can be submitted.
"""
import os
from enum import Enum
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import propose_and_confirm as pac
from ads_mcp import utils

campaigns_mcp = FastMCP("campaigns")


class CampaignAction(str, Enum):
    CREATE_PAUSED = "CREATE_PAUSED"
    RENAME = "RENAME"
    PAUSE = "PAUSE"
    RESUME = "RESUME"


class BudgetAction(str, Enum):
    CREATE = "CREATE"
    UPDATE_AMOUNT = "UPDATE_AMOUNT"


def _account(customer_id: str | int) -> str:
    customer = utils.clean_customer_id(customer_id)
    allowed = utils.clean_customer_id(
        os.environ.get("GOOGLE_ADS_MCP_WRITE_CUSTOMER_ID", "")
    )
    if not customer or not allowed or customer != allowed:
        raise ToolError("Campaign writes are disabled for this account. Configure GOOGLE_ADS_MCP_WRITE_CUSTOMER_ID to explicitly authorize this customer.")
    return customer


def _id(value: str | int | None, field: str) -> str:
    if value is None or not str(value).isdigit() or int(value) <= 0:
        raise ToolError(f"{field} must be a positive numeric ID.")
    return str(value)


def _money(daily_amount_micros: int | None) -> int:
    if type(daily_amount_micros) is not int or daily_amount_micros < 10000:
        raise ToolError("daily_amount_micros must be an integer >= 10000 (0.01 in account currency).")
    if daily_amount_micros > 1000000000000:
        raise ToolError("Budget amount exceeds the connector's safety ceiling.")
    return daily_amount_micros


@campaigns_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def manage_campaign(
    customer_id: str,
    action: CampaignAction,
    campaign_id: str | None = None,
    name: str | None = None,
    budget_id: str | None = None,
    contains_eu_political_advertising: bool | None = None,
    login_customer_id: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Create a PAUSED Search campaign, rename one, or pause/resume one.

    ALWAYS call with validate_only=True first, show the change_items, and obtain
    explicit user approval. Only then call with validate_only=False and the
    checksum of the approved proposal. RESUME can cause advertising spending.
    CREATE_PAUSED only builds an empty paused Search campaign: no ads or
    targeting are automatically created.

    budget_id is required for CREATE_PAUSED; prepare it with manage_budget.
    contains_eu_political_advertising must be explicitly declared for creation.
    """
    customer = _account(customer_id)
    action = CampaignAction(action)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "CampaignService")
    op = client.get_type("CampaignOperation")
    change_fields = {}
    target = ""
    if action == CampaignAction.CREATE_PAUSED:
        if not name or not name.strip() or len(name.strip()) > 255:
            raise ToolError("CREATE_PAUSED requires a campaign name of at most 255 characters.")
        if contains_eu_political_advertising is None:
            raise ToolError("Declare contains_eu_political_advertising explicitly.")
        budget = _id(budget_id, "budget_id")
        campaign = op.create
        campaign.name = name.strip()
        campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
        campaign.status = client.enums.CampaignStatusEnum.PAUSED
        campaign.campaign_budget = f"customers/{customer}/campaignBudgets/{budget}"
        campaign.manual_cpc = client.get_type("ManualCpc")
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = False
        campaign.network_settings.target_partner_search_network = False
        campaign.network_settings.target_content_network = False
        declaration = ("CONTAINS_EU_POLITICAL_ADVERTISING" if contains_eu_political_advertising
                       else "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING")
        campaign.contains_eu_political_advertising = getattr(
            client.enums.EuPoliticalAdvertisingStatusEnum, declaration
        )
        target = campaign.name
        change_fields = {"Name": campaign.name, "Channel": "SEARCH",
                         "Status": "PAUSED", "Budget ID": budget,
                         "EU political advertising": declaration}
    else:
        ident = _id(campaign_id, "campaign_id")
        target = f"customers/{customer}/campaigns/{ident}"
        campaign = op.update
        campaign.resource_name = target
        if action == CampaignAction.RENAME:
            if not name or not name.strip() or len(name.strip()) > 255:
                raise ToolError("RENAME requires a name of at most 255 characters.")
            campaign.name = name.strip()
            op.update_mask.paths.append("name")
            change_fields = {"Name": name.strip()}
        else:
            status = "PAUSED" if action == CampaignAction.PAUSE else "ENABLED"
            campaign.status = getattr(client.enums.CampaignStatusEnum, status)
            op.update_mask.paths.append("status")
            change_fields = {"Status": status, "Spending impact":
                             "May begin ad serving and incur costs" if status == "ENABLED"
                             else "Stops campaign ad serving"}

    req = client.get_type("MutateCampaignsRequest")
    req.customer_id = customer
    req.operations.append(op)
    req.partial_failure = False
    items = pac.build_change_items(action.value, "Campaign", target, change_fields)
    payload = {"tool": "manage_campaign", "action": action.value, "operation": op,
               "login_customer_id": login_customer_id}

    def validate():
        req.validate_only = True
        service.mutate_campaigns(request=req)

    def commit():
        req.validate_only = False
        result = service.mutate_campaigns(request=req)
        return f"Campaign change applied: {result.results[0].resource_name}"

    return pac.propose_and_confirm(
        customer_id=customer, validate_only=validate_only, checksum=checksum,
        operation_payload=payload, change_items=items,
        validate_fn=validate, commit_fn=commit, error_prefix="Campaign mutation failed"
    )


@campaigns_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def manage_budget(
    customer_id: str,
    action: BudgetAction,
    daily_amount_micros: int,
    budget_id: str | None = None,
    name: str | None = None,
    login_customer_id: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Create or change a daily campaign budget. Amount is in micros.

    For USD, $10.00/day is 10000000 micros. Google can spend more than
    the average daily amount on individual days subject to its billing rules.
    Some budgets are shared by multiple campaigns. Check linked campaigns
    before updating. ALWAYS validate, present exact changes and get explicit
    user approval before confirming with the supplied checksum.
    """
    customer = _account(customer_id)
    action = BudgetAction(action)
    amount = _money(daily_amount_micros)
    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "CampaignBudgetService")
    op = client.get_type("CampaignBudgetOperation")
    if action == BudgetAction.CREATE:
        if not name or not name.strip() or len(name.strip()) > 255:
            raise ToolError("CREATE requires a budget name of at most 255 characters.")
        budget = op.create
        budget.name = name.strip()
        budget.amount_micros = amount
        budget.explicitly_shared = False
        target = budget.name
        changes = {"Name": budget.name, "Daily amount (micros)": amount,
                   "Shared": False}
    else:
        ident = _id(budget_id, "budget_id")
        target = f"customers/{customer}/campaignBudgets/{ident}"
        budget = op.update
        budget.resource_name = target
        budget.amount_micros = amount
        op.update_mask.paths.append("amount_micros")
        changes = {"Daily amount (micros)": amount,
                   "Spending impact": "Changes daily budget; may affect all campaigns sharing this budget"}
    req = client.get_type("MutateCampaignBudgetsRequest")
    req.customer_id = customer
    req.operations.append(op)
    req.partial_failure = False
    items = pac.build_change_items(action.value, "Campaign Budget", target, changes)
    payload = {"tool": "manage_budget", "action": action.value,
               "operation": op, "login_customer_id": login_customer_id}

    def validate():
        req.validate_only = True
        service.mutate_campaign_budgets(request=req)

    def commit():
        req.validate_only = False
        result = service.mutate_campaign_budgets(request=req)
        return f"Budget change applied: {result.results[0].resource_name}"

    return pac.propose_and_confirm(
        customer_id=customer, validate_only=validate_only, checksum=checksum,
        operation_payload=payload, change_items=items,
        validate_fn=validate, commit_fn=commit, error_prefix="Budget mutation failed"
    )
