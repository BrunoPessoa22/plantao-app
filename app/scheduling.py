"""Deterministic slot engine: availability = working hours minus booked appointments."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .clinic import ClinicConfig

TZ = ZoneInfo("America/Sao_Paulo")
SLOT_MIN = 30
SEARCH_DAYS = 14
MIN_LEAD = timedelta(hours=1)  # never offer or accept slots less than 1h away

WEEKDAYS_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def fmt_pt(dt: datetime) -> str:
    return f"{WEEKDAYS_PT[dt.weekday()]} {dt.strftime('%d/%m')} às {dt.strftime('%H:%M')}"


def now_local() -> datetime:
    return datetime.now(TZ)


def local_day_range_utc(day: date | None = None) -> tuple[str, str, str]:
    """(start_utc_iso, end_utc_iso, label) for one clinic-local calendar day."""
    d = day or now_local().date()
    start = datetime(d.year, d.month, d.day, tzinfo=TZ)
    end = start + timedelta(days=1)
    return (start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat(), d.isoformat())


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
    cfg: ClinicConfig, service_id: str, professional_id: str | None = None,
    limit: int = 8, a_partir_de: str | None = None, periodo: str | None = None,
) -> list[dict]:
    """Next available slots; optional date floor (YYYY-MM-DD) and periodo ('manha'|'tarde')."""
    service = cfg.service(service_id)
    if service is None:
        return []
    pros = [p for p in cfg.professionals if service_id in p.services]
    if professional_id:
        pros = [p for p in pros if p.id == professional_id]
    floor = now_local() + MIN_LEAD
    if a_partir_de:
        try:
            d = date.fromisoformat(a_partir_de)
            floor = max(floor, datetime(d.year, d.month, d.day, tzinfo=TZ))
        except ValueError:
            pass
    results: list[dict] = []
    for day_offset in range(SEARCH_DAYS):
        day = (floor + timedelta(days=day_offset)).replace(hour=0, minute=0)
        for pro in pros:
            taken = db.booked_starts(pro.id)
            for slot in _day_slots(cfg, day):
                if slot < floor or slot.isoformat() in taken:
                    continue
                if periodo == "manha" and slot.hour >= 12:
                    continue
                if periodo == "tarde" and slot.hour < 12:
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
    if conv.lower() != "particular":
        matched = next((c for c in service.convenios if c.casefold() == conv.casefold()), None)
        if matched is None:
            return {"ok": False, "erro": "convenio_nao_aceito_para_este_servico",
                    "convenios_aceitos": service.convenios + ["particular"]}
        conv = matched
    else:
        conv = "particular"
    try:
        start = datetime.fromisoformat(start_iso)
    except ValueError:
        return {"ok": False, "erro": "data_invalida"}
    # Canonicalize to clinic timezone: validation frame, uniqueness key, and
    # stored string must all live in America/Sao_Paulo
    start = start.replace(tzinfo=TZ) if start.tzinfo is None else start.astimezone(TZ)
    if start < now_local() + MIN_LEAD:
        return {"ok": False, "erro": "antecedencia_minima_1h"}
    if start > now_local() + timedelta(days=SEARCH_DAYS):
        return {"ok": False, "erro": "fora_da_janela_de_agendamento"}
    if start not in _day_slots(cfg, start.replace(hour=0, minute=0)):
        return {"ok": False, "erro": "fora_do_horario_de_funcionamento"}
    appt_id = db.create_appointment(
        cfg.id, professional_id, service_id, patient_name, patient_contact, conv, start.isoformat(),
    )
    if appt_id is None:
        return {"ok": False, "erro": "horario_acabou_de_ser_ocupado"}
    return {
        "ok": True, "id": appt_id, "profissional": pro.name, "servico": service.name,
        "inicio_iso": start.isoformat(), "legivel": fmt_pt(start), "convenio": conv,
    }


def cancel_next(cfg: ClinicConfig, patient_contact: str) -> dict:
    """Cancel the patient's next upcoming appointment (24h policy enforced)."""
    appt = db.next_appointment_for(patient_contact, now_local().isoformat())
    if appt is None:
        return {"ok": False, "erro": "nenhuma_consulta_futura_encontrada"}
    start = datetime.fromisoformat(appt["start_iso"])
    if start - now_local() < timedelta(hours=24):
        return {"ok": False, "erro": "cancelamento_so_ate_24h_antes",
                "consulta": {"inicio_iso": appt["start_iso"], "legivel": fmt_pt(start)}}
    db.cancel_appointment(appt["id"])
    pro = cfg.professional(appt["professional_id"])
    return {"ok": True, "cancelada": {
        "profissional": pro.name if pro else appt["professional_id"],
        "inicio_iso": appt["start_iso"], "legivel": fmt_pt(start),
    }}
