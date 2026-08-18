"""Plantão MVP — FastAPI app: web demo chat, WhatsApp webhook, admin, digest."""
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import brain, db, whatsapp
from .clinic import DEMO_CLINIC
from .settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("plantao")

app = FastAPI(title="Plantão MVP", docs_url=None, redoc_url=None)


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
    reply = await brain.reply(DEMO_CLINIC, conv_id, f"web:{body.session_id}", body.message)
    conn = db.get_conn()
    handed = conn.execute(
        "SELECT handed_off FROM conversations WHERE id=?", (conv_id,)
    ).fetchone()["handed_off"]
    return ChatOut(reply=reply, handed_off=bool(handed))


# --- WhatsApp Cloud API webhook (official WABA) ---

@app.get("/webhook/whatsapp")
async def wa_verify(request: Request) -> PlainTextResponse:
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.wa_verify_token:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="verify token mismatch")


@app.post("/webhook/whatsapp")
async def wa_incoming(request: Request) -> dict:
    payload = await request.json()
    for msg in whatsapp.parse_incoming(payload):
        if not msg["from"] or not msg["text"]:
            continue
        conv_id = db.get_or_create_conversation("whatsapp", msg["from"])
        reply = await brain.reply(DEMO_CLINIC, conv_id, msg["from"], msg["text"])
        await whatsapp.send_text(msg["from"], reply)
    return {"ok": True}


# --- Admin / digest ---

def _require_admin(token: str) -> None:
    if token != settings.admin_token:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/api/admin/overview")
async def admin_overview(token: str = Query(...)) -> dict:
    _require_admin(token)
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "digest": db.digest(today),
        "conversas": db.recent_conversations(),
        "agendamentos": db.upcoming_appointments(),
    }


@app.get("/api/digest")
async def digest_endpoint(token: str = Query(...), date: str | None = None) -> dict:
    _require_admin(token)
    return db.digest(date or datetime.now(timezone.utc).date().isoformat())


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse("static/admin.html")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
