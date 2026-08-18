"""Plantão MVP — FastAPI app: web demo chat, WhatsApp webhook, admin, digest."""
import logging
import secrets
from collections import OrderedDict
from datetime import date

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import brain, db, scheduling, whatsapp
from .clinic import DEMO_CLINIC
from .settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("plantao")

app = FastAPI(title="Plantão MVP", docs_url=None, redoc_url=None)

HANDED_OFF_REPLY = "A equipe da clínica já foi acionada e continua por aqui em instantes."

# small LRU of processed WhatsApp message ids (Meta redelivers on slow 200s)
_seen_wa_ids: OrderedDict[str, None] = OrderedDict()
_SEEN_MAX = 2000


class ChatIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class ChatOut(BaseModel):
    reply: str
    handed_off: bool


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "clinic": DEMO_CLINIC.name,
        "model": settings.model,
        "ai_configured": settings.anthropic_key_present,
        "whatsapp_enabled": settings.whatsapp_enabled,
    }


@app.post("/api/chat", response_model=ChatOut)
async def chat(body: ChatIn) -> ChatOut:
    conv_id = db.get_or_create_conversation("web", body.session_id)
    if db.is_handed_off(conv_id):
        db.add_message(conv_id, "user", body.message)
        return ChatOut(reply=HANDED_OFF_REPLY, handed_off=True)
    reply = await brain.reply(DEMO_CLINIC, conv_id, f"web:{body.session_id}", body.message)
    return ChatOut(reply=reply, handed_off=db.is_handed_off(conv_id))


# --- WhatsApp Cloud API webhook (official WABA) ---

@app.get("/webhook/whatsapp")
async def wa_verify(request: Request) -> PlainTextResponse:
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.wa_verify_token:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="verify token mismatch")


async def _process_wa_message(msg: dict) -> None:
    conv_id = db.get_or_create_conversation("whatsapp", msg["from"])
    if msg["type"] != "text":
        await whatsapp.send_text(msg["from"], "Por enquanto só consigo ler mensagens de texto — pode escrever, por favor?")
        return
    if db.is_handed_off(conv_id):
        db.add_message(conv_id, "user", msg["text"])
        await whatsapp.send_text(msg["from"], HANDED_OFF_REPLY)
        return
    reply = await brain.reply(DEMO_CLINIC, conv_id, msg["from"], msg["text"])
    await whatsapp.send_text(msg["from"], reply)


@app.post("/webhook/whatsapp")
async def wa_incoming(request: Request, background: BackgroundTasks) -> dict:
    raw = await request.body()
    if not whatsapp.signature_valid(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="invalid signature")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid json")
    for msg in whatsapp.parse_incoming(payload):
        if not msg["from"]:
            continue
        if msg["id"]:
            if msg["id"] in _seen_wa_ids:
                continue
            _seen_wa_ids[msg["id"]] = None
            while len(_seen_wa_ids) > _SEEN_MAX:
                _seen_wa_ids.popitem(last=False)
        # respond 200 fast; Meta redelivers on slow responses
        background.add_task(_process_wa_message, msg)
    return {"ok": True}


# --- Admin / digest ---

def _require_admin(authorization: str | None) -> None:
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
    if not secrets.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/api/admin/overview")
async def admin_overview(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    start, end, label = scheduling.local_day_range_utc()
    return {
        "digest": db.digest(start, end, label),
        "conversas": db.recent_conversations(),
        "agendamentos": db.upcoming_appointments(),
    }


@app.get("/api/digest")
async def digest_endpoint(
    authorization: str | None = Header(default=None), date_str: str | None = None,
) -> dict:
    _require_admin(authorization)
    day = date.fromisoformat(date_str) if date_str else None
    start, end, label = scheduling.local_day_range_utc(day)
    return db.digest(start, end, label)


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse("static/admin.html")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
