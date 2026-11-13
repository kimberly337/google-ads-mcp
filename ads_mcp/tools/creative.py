# Copyright 2026 E Factor Leadership.
# Licensed under the Apache License, Version 2.0.
"""Guarded responsive Search ad creative editing."""

from urllib.parse import urlparse

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import propose_and_confirm as pac
from ads_mcp import utils

creative_mcp = FastMCP("creative")


def _id(value, field):
    digits = str(value).replace("-", "").strip()
    if not digits.isdigit():
        raise ToolError(field + " must be numeric.")
    return digits


@creative_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def replace_responsive_search_ad_copy(
    customer_id: str, ad_id: str,
    headlines: list[str], descriptions: list[str],
    final_url: str | None = None,
    login_customer_id: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Replace responsive search ad headlines and descriptions (all values in each list).
    Updates are not incremental: supply every headline and description to retain.
    Defaults to validate_only=True, with NO changes committed. Requires a fresh
    proposal and explicit user approval before calling validate_only=False.
    """
    cid = _id(customer_id, "customer_id")
    aid = _id(ad_id, "ad_id")
    if not 3 <= len(headlines) <= 15 or not 2 <= len(descriptions) <= 4:
        raise ToolError("Provide 3-15 complete headlines and 2-4 complete descriptions.")
    headlines = [str(x).strip() for x in headlines]
    descriptions = [str(x).strip() for x in descriptions]
    if any(not x or len(x) > 30 for x in headlines):
        raise ToolError("Each headline must be 1-30 characters.")
    if any(not x or len(x) > 90 for x in descriptions):
        raise ToolError("Each description must be 1-90 characters.")
    if len(set(headlines)) != len(headlines):
        raise ToolError("Headlines must be distinct.")
    if final_url is not None:
        parsed = urlparse(final_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ToolError("final_url must be a valid HTTPS URL without credentials.")

    client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(client, "AdService")
    operation = client.get_type("AdOperation")
    ad = operation.update
    rn = f"customers/{cid}/ads/{aid}"
    ad.resource_name = rn
    for line in headlines:
        asset = client.get_type("AdTextAsset")
        asset.text = line
        ad.responsive_search_ad.headlines.append(asset)
    for line in descriptions:
        asset = client.get_type("AdTextAsset")
        asset.text = line
        ad.responsive_search_ad.descriptions.append(asset)
    operation.update_mask.paths.extend([
        "responsive_search_ad.headlines", "responsive_search_ad.descriptions"
    ])
    if final_url is not None:
        ad.final_urls.append(final_url)
        operation.update_mask.paths.append("final_urls")
    request = client.get_type("MutateAdsRequest")
    request.customer_id = cid
    request.operations.append(operation)
    request.validate_only = True

    payload = {"tool": "replace_responsive_search_ad_copy", "ad_id": aid,
               "headlines": headlines, "descriptions": descriptions,
               "final_url": final_url}
    changes = pac.build_change_items("UPDATE", "Responsive Search Ad", rn, {
        "Replacement headlines (complete)": headlines,
        "Replacement descriptions (complete)": descriptions,
        "New final URL": final_url,
    })
    def _validate():
        request.validate_only = True
        service.mutate_ads(request=request)
    def _commit():
        request.validate_only = False
        response = service.mutate_ads(request=request)
        return "Updated ad: " + response.results[0].resource_name

    return pac.propose_and_confirm(
        customer_id=cid, validate_only=validate_only, checksum=checksum,
        operation_payload=payload, change_items=changes,
        validate_fn=_validate, commit_fn=_commit,
        error_prefix="Responsive Search Ad update failed",
    )
