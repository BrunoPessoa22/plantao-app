"""SQLite persistence for conversations, messages, appointments, and events."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .settings import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,             -- 'web' | 'whatsapp'
    external_id TEXT NOT NULL,         -- web session id or WA phone number
    created_at TEXT NOT NULL,
    handed_off INTEGER NOT NULL DEFAULT 0,
    UNIQUE(channel, external_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,                -- 'user' | 'assistant'
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clinic_id TEXT NOT NULL,
    professional_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    patient_name TEXT NOT NULL,
    patient_contact TEXT NOT NULL,
    convenio TEXT NOT NULL,            -- plan name or 'particular'
    start_iso TEXT NOT NULL,           -- canonical America/Sao_Paulo ISO datetime
    status TEXT NOT NULL DEFAULT 'confirmada',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_slot
    ON appointments(professional_id, start_iso) WHERE status='confirmada';
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    kind TEXT NOT NULL,                -- 'agendamento' | 'cancelamento' | 'transbordo' | 'reclamacao' | 'ocorrencia'
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = settings.data_dir / "plantao.db"
        _conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_or_create_conversation(channel: str, external_id: str) -> int:
    with _lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT id FROM conversations WHERE channel=? AND external_id=?",
            (channel, external_id),
        ).fetchone()
        if row:
            return int(row["id"])
        try:
            cur = conn.execute(
                "INSERT INTO conversations(channel, external_id, created_at) VALUES(?,?,?)",
                (channel, external_id, _now()),
            )
            conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id FROM conversations WHERE channel=? AND external_id=?",
                (channel, external_id),
            ).fetchone()
            return int(row["id"])


def add_message(conversation_id: int, role: str, text: str) -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO messages(conversation_id, role, text, created_at) VALUES(?,?,?,?)",
            (conversation_id, role, text, _now()),
        )
        conn.commit()


def get_history(conversation_id: int, limit: int = 31) -> list[dict[str, str]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, text FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    msgs = [{"role": r["role"], "content": r["text"]} for r in reversed(rows)]
    # Messages API requires messages[0] to be a user turn
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def is_handed_off(conversation_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT handed_off FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    return bool(row and row["handed_off"])


def mark_handed_off(conversation_id: int) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE conversations SET handed_off=1 WHERE id=?", (conversation_id,))
        conn.commit()


def add_event(conversation_id: int | None, kind: str, payload: dict) -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO events(conversation_id, kind, payload, created_at) VALUES(?,?,?,?)",
            (conversation_id, kind, json.dumps(payload, ensure_ascii=False), _now()),
        )
        conn.commit()


def booked_starts(professional_id: str) -> set[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT start_iso FROM appointments WHERE professional_id=? AND status='confirmada'",
        (professional_id,),
    ).fetchall()
    return {r["start_iso"] for r in rows}


def create_appointment(
    clinic_id: str, professional_id: str, service_id: str,
    patient_name: str, patient_contact: str, convenio: str, start_iso: str,
) -> int | None:
    """Insert an appointment; returns id, or None if the slot was just taken."""
    with _lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO appointments(clinic_id, professional_id, service_id, patient_name,"
                " patient_contact, convenio, start_iso, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (clinic_id, professional_id, service_id, patient_name,
                 patient_contact, convenio, start_iso, _now()),
            )
            conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None


def next_appointment_for(patient_contact: str, after_iso: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM appointments WHERE patient_contact=? AND status='confirmada'"
        " AND start_iso >= ? ORDER BY start_iso ASC LIMIT 1",
        (patient_contact, after_iso),
    ).fetchone()
    return dict(row) if row else None


def cancel_appointment(appointment_id: int) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE appointments SET status='cancelada' WHERE id=?", (appointment_id,))
        conn.commit()


def upcoming_appointments(limit: int = 50) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM appointments WHERE status='confirmada' ORDER BY start_iso ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def digest(start_utc_iso: str, end_utc_iso: str, label: str) -> dict:
    """Counts for a UTC time range (computed by the caller from the clinic's local day)."""
    conn = get_conn()
    convs = conn.execute(
        "SELECT COUNT(DISTINCT conversation_id) AS c FROM messages WHERE created_at >= ? AND created_at < ?",
        (start_utc_iso, end_utc_iso),
    ).fetchone()["c"]
    def count(kind: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE kind=? AND created_at >= ? AND created_at < ?",
            (kind, start_utc_iso, end_utc_iso),
        ).fetchone()["c"]
    return {
        "date": label,
        "conversas": convs,
        "agendamentos": count("agendamento"),
        "cancelamentos": count("cancelamento"),
        "transbordos": count("transbordo"),
        "reclamacoes": count("reclamacao"),
    }


def recent_conversations(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.id, c.channel, c.external_id, c.handed_off,
                  (SELECT text FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_text,
                  (SELECT created_at FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_at
           FROM conversations c ORDER BY last_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
