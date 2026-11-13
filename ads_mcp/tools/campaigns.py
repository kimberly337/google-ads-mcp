# Copyright 2026 E Factor Leadership.
# Licensed under the Apache License, Version 2.0.
"""Guarded Google Ads campaign status and daily budget management tools."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import propose_and_confirm as pac
from ads_mcp import utils

campaigns_mcp = FastMCP("campaigns")


def _digits(value: str | int, label: str) -> str:
    cleaned = str(value).replace("-", "").strip()
    if not cleaned.isdigit() or not cleaned:
        raise ToolError(f"{label} must be a numeric Google Ads identifier.")
    return cleaned


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def set_campaign_status(
    customer_id: str | int,
    campaign_id: str | int,
    status: str,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Pause or enable an EXISTING campaign. Defaults to validation only.

    Before committing, present the validated change_items to the user and
    obtain their explicit approval. Commit by repeating exactly the same
    arguments with validate_only=False and the returned checksum.
    Enabling a campaign may cause advertising spend. Never infer approval.
    """
    cid = _digits(customer_id, "customer_id")
    campid = _digits(campaign_id, "campaign_id")
    wanted = status.strip().upper()
    if wanted not in ("PAUSED", "ENABLED"):
        raise ToolError("status must be PAUSED or ENABLED.")

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "CampaignService")
    operation = client.get_type("CampaignOperation")
    resource_name = f"customers/{cid}/campaigns/{campid}"
    operation.update.resource_name = resource_name
    operation.update.status = wanted
    operation.update_mask.paths.append("status")

    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    request.validate_only = True

    payload = {"tool": "set_campaign_status", "campaign_id": campid, "status": wanted}
    changes = pac.build_change_items(
        "UPDATE", "Campaign", resource_name,
        {"New status": wanted, "Spend warning": "ENABLED can incur advertising spend" if wanted == "ENABLED" else "PAUSED stops serving"},
    )

    def validate() -> None:
        request.validate_only = True
        service.mutate_campaigns(request=request)

    def commit() -> str:
        request.validate_only = False
        response = service.mutate_campaigns(request=request)
        return f"Updated campaign status to {wanted}: {response.results[0].resource_name}"

    return pac.propose_and_confirm(
        customer_id=cid, validate_only=validate_only, checksum=checksum,
        operation_payload=payload, change_items=changes,
        validate_fn=validate, commit_fn=commit,
        error_prefix="Campaign status update failed",
    )


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def set_daily_budget(
    customer_id: str | int,
    budget_id: str | int,
    daily_budget_amount: str,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Set an EXISTING Google Ads campaign budget's daily amount.

    Amount is in account currency units, such as "10.00", NOT micros.
    Budget may be shared by multiple campaigns; check associations first.
    Google Ads daily budgets are average daily budgets, not guaranteed
    daily hard spending caps. Show proposed spend and obtain explicit
    user approval before committing.
    """
    cid = _digits(customer_id, "customer_id")
    bid = _digits(budget_id, "budget_id")
    try:
        amount = Decimal(str(daily_budget_amount))
    except (InvalidOperation, ValueError):
        raise ToolError("daily_budget_amount must be a positive decimal number.")
    if not amount.is_finite() or amount <= 0:
        raise ToolError("daily_budget_amount must be finite and greater than zero.")
    if amount > Decimal("100000"):
        raise ToolError("Amount exceeds the protective 100,000-unit per-day limit.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ToolError("Specify no more than two decimal places.")
    micros = int((amount * Decimal("1000000")).to_integral_value(rounding=ROUND_HALF_UP))

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "CampaignBudgetService")
    operation = client.get_type("CampaignBudgetOperation")
    resource_name = f"customers/{cid}/campaignBudgets/{bid}"
    operation.update.resource_name = resource_name
    operation.update.amount_micros = micros
    operation.update_mask.paths.append("amount_micros")

    request = client.get_type("MutateCampaignBudgetsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    request.validate_only = True

    payload = {"tool": "set_daily_budget", "budget_id": bid, "amount_micros": micros}
    changes = pac.build_change_items(
        "UPDATE", "Campaign budget", resource_name,
        {"New average daily amount (account currency)": f"{amount:.2f}",
         "Important": "Shared budgets affect all linked campaigns; Google may spend more on individual days."},
    )

    def validate() -> None:
        request.validate_only = True
        service.mutate_campaign_budgets(request=request)

    def commit() -> str:
        request.validate_only = False
        response = service.mutate_campaign_budgets(request=request)
        return f"Updated daily budget: {response.results[0].resource_name}"

    return pac.propose_and_confirm(
        customer_id=cid, validate_only=validate_only, checksum=checksum,
        operation_payload=payload, change_items=changes,
        validate_fn=validate, commit_fn=commit,
        error_prefix="Daily budget update failed",
    )
