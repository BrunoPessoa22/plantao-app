"""Meta WhatsApp Cloud API adapter (official WABA channel).

Inactive until WA_TOKEN + WA_PHONE_NUMBER_ID are set — the same brain then serves
the client's own official WhatsApp number with zero code changes.
"""
import logging

import httpx

from .settings import settings

log = logging.getLogger("plantao.whatsapp")

GRAPH_URL = "https://graph.facebook.com/v21.0"


def parse_incoming(payload: dict) -> list[dict]:
    """Extract text messages from a webhook POST. Returns [{'from': phone, 'text': body}]."""
    out: list[dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") == "text":
                    out.append({"from": msg.get("from", ""), "text": msg.get("text", {}).get("body", "")})
    return out


async def send_text(to: str, body: str) -> bool:
    if not settings.whatsapp_enabled:
        log.info("whatsapp disabled; dropping outbound to %s", to)
        return False
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{GRAPH_URL}/{settings.wa_phone_number_id}/messages",
            headers={"Authorization": f"Bearer {settings.wa_token}"},
            json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}},
        )
    if resp.status_code >= 300:
        log.error("WA send failed %s: %s", resp.status_code, resp.text[:300])
        return False
    return True
