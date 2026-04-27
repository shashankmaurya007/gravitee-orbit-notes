"""HubSpot CRM API client — creates notes associated with companies."""
from __future__ import annotations

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_BASE = settings.hubspot_base_url
_HEADERS = {
    "Authorization": f"Bearer {settings.hubspot_api_key}",
    "Content-Type": "application/json",
}

# HubSpot association type: note → company (typeId 190 in the v3 API)
_NOTE_TO_COMPANY_TYPE_ID = 190


async def create_note(
    hubspot_company_id: str,
    note_body: str,
) -> str:
    """Create a HubSpot note and associate it with the given company. Returns the note ID."""
    url = f"{_BASE}/crm/v3/objects/notes"
    payload = {
        "properties": {
            "hs_note_body": note_body,
            "hs_timestamp": _now_ms(),
        },
        "associations": [
            {
                "to": {"id": hubspot_company_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": _NOTE_TO_COMPANY_TYPE_ID,
                    }
                ],
            }
        ],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=_HEADERS, json=payload)
        resp.raise_for_status()
        data = resp.json()

    note_id: str = data["id"]
    logger.info("Created HubSpot note %s for company %s", note_id, hubspot_company_id)
    return note_id


async def fetch_all_owners() -> dict[str, str]:
    """Return {owner_id: full_name}. Returns empty dict if the token lacks the owners scope."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{_BASE}/crm/v3/owners", headers=_HEADERS, params={"limit": 500})
            resp.raise_for_status()
        owners = {}
        for o in resp.json().get("results", []):
            owners[str(o["id"])] = f"{o.get('firstName', '')} {o.get('lastName', '')}".strip()
        return owners
    except Exception:
        logger.warning("Could not fetch HubSpot owners (token may lack crm.objects.owners.read scope)")
        return {}


async def fetch_company_fields(
    hubspot_company_id: str,
    owner_map: dict[str, str],
) -> dict:
    """Fetch CSM, TAM, Pod, AE for a HubSpot company. Returns dict with keys csm/tam/pod/ae_name."""
    props = "customer_success_manager,technical_account_manager,company_pod,hubspot_owner_id"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_BASE}/crm/v3/objects/companies/{hubspot_company_id}",
            headers=_HEADERS,
            params={"properties": props},
        )
        if resp.status_code != 200:
            return {}
    p = resp.json().get("properties", {})
    owner_id = p.get("hubspot_owner_id") or ""
    return {
        "csm": p.get("customer_success_manager") or None,
        "tam": p.get("technical_account_manager") or None,
        "pod": p.get("company_pod") or None,
        "ae_name": owner_map.get(owner_id) or None,
    }


def _now_ms() -> int:
    from datetime import datetime, timezone
    return int(datetime.now(timezone.utc).timestamp() * 1000)
