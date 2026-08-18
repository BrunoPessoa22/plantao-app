"""Deterministic slot engine: availability = working hours minus booked appointments."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .clinic import ClinicConfig

TZ = ZoneInfo("America/Sao_Paulo")
SLOT_MIN = 30
SEARCH_DAYS = 14

WEEKDAYS_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def fmt_pt(dt: datetime) -> str:
    return f"{WEEKDAYS_PT[dt.weekday()]} {dt.strftime('%d/%m')} às {dt.strftime('%H:%M')}"


def now_local() -> datetime:
    return datetime.now(TZ)


def _day_slots(cfg: ClinicConfig, day: datetime) -> list[datetime]:
    slots: list[datetime] = []
    for wh in cfg.hours:
        if wh.weekday != day.weekday():
            continue
        start_h, start_m = map(int, wh.start.split(":"))
        end_h, end_m = map(int, wh.end.split(":"))
        cur = day.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end = day.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        while cur + timedelta(minutes=SLOT_MIN) <= end:
            slots.append(cur)
            cur += timedelta(minutes=SLOT_MIN)
    return slots


def find_slots(
    cfg: ClinicConfig, service_id: str, professional_id: str | None = None, limit: int = 5,
) -> list[dict]:
    """Next available slots for a service, optionally restricted to one professional."""
    service = cfg.service(service_id)
    if service is None:
        return []
    pros = [p for p in cfg.professionals if service_id in p.services]
    if professional_id:
        pros = [p for p in pros if p.id == professional_id]
    results: list[dict] = []
    start = now_local() + timedelta(hours=1)  # never offer slots less than 1h away
    for day_offset in range(SEARCH_DAYS):
        day = (start + timedelta(days=day_offset)).replace(hour=0, minute=0)
        for pro in pros:
            taken = db.booked_starts(pro.id)
            for slot in _day_slots(cfg, day):
                if slot < start or slot.isoformat() in taken:
                    continue
                results.append({
                    "professional_id": pro.id,
                    "professional": pro.name,
                    "inicio_iso": slot.isoformat(),
                    "legivel": fmt_pt(slot),
                })
        if len(results) >= limit:
            break
    results.sort(key=lambda r: r["inicio_iso"])
    return results[:limit]


def book(
    cfg: ClinicConfig, service_id: str, professional_id: str, start_iso: str,
    patient_name: str, patient_contact: str, convenio: str,
) -> dict:
    """Validate and persist a booking. Returns {ok, ...} — errors are data, not exceptions."""
    service = cfg.service(service_id)
    pro = cfg.professional(professional_id)
    if service is None or pro is None or service_id not in pro.services:
        return {"ok": False, "erro": "servico_ou_profissional_invalido"}
    conv = convenio.strip()
    if conv.lower() != "particular" and conv not in service.convenios:
        return {"ok": False, "erro": "convenio_nao_aceito_para_este_servico",
                "convenios_aceitos": service.convenios + ["particular"]}
    try:
        start = datetime.fromisoformat(start_iso)
    except ValueError:
        return {"ok": False, "erro": "data_invalida"}
    if start.tzinfo is None:
        start = start.replace(tzinfo=TZ)
    if start < now_local():
        return {"ok": False, "erro": "horario_no_passado"}
    if start not in _day_slots(cfg, start.replace(hour=0, minute=0)):
        return {"ok": False, "erro": "fora_do_horario_de_funcionamento"}
    appt_id = db.create_appointment(
        cfg.id, professional_id, service_id, patient_name, patient_contact, conv, start.isoformat(),
    )
    if appt_id is None:
        return {"ok": False, "erro": "horario_acabou_de_ser_ocupado"}
    return {
        "ok": True, "id": appt_id, "profissional": pro.name, "servico": service.name,
        "inicio_iso": start.isoformat(), "legivel": fmt_pt(start),
        "convenio": conv,
    }
