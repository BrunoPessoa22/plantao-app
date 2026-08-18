"""Clinic configuration — the per-client 'contract' the AI employee works from.

In production each client gets one of these (loaded from DB / onboarding).
The MVP ships one demo clinic so the whole loop is testable end to end.
"""
from pydantic import BaseModel


class Professional(BaseModel):
    id: str
    name: str
    title: str
    services: list[str]  # service ids


class Service(BaseModel):
    id: str
    name: str
    price_particular: int  # BRL
    convenios: list[str]  # accepted insurance plans for this service
    duration_min: int = 30


class WorkingHours(BaseModel):
    # weekday: 0=Monday .. 6=Sunday; ranges in "HH:MM" 24h local time
    weekday: int
    start: str
    end: str


class ClinicConfig(BaseModel):
    id: str
    name: str
    address: str
    phone_display: str
    convenios: list[str]
    services: list[Service]
    professionals: list[Professional]
    hours: list[WorkingHours]
    policies: str

    def service(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)

    def professional(self, professional_id: str) -> Professional | None:
        return next((p for p in self.professionals if p.id == professional_id), None)


DEMO_CLINIC = ClinicConfig(
    id="clinica-vida",
    name="Clínica Vida",
    address="Rua Augusta 1200, Consolação, São Paulo/SP",
    phone_display="(11) 3000-0000",
    convenios=["Unimed", "Bradesco Saúde"],
    services=[
        Service(id="derma", name="Consulta dermatologia", price_particular=380,
                convenios=["Unimed"], duration_min=30),
        Service(id="geral", name="Consulta clínica geral", price_particular=260,
                convenios=["Unimed", "Bradesco Saúde"], duration_min=30),
    ],
    professionals=[
        Professional(id="marina", name="Dra. Marina Duarte", title="Dermatologista", services=["derma"]),
        Professional(id="rafael", name="Dr. Rafael Nogueira", title="Clínico geral", services=["geral"]),
    ],
    hours=[
        WorkingHours(weekday=0, start="08:00", end="19:00"),
        WorkingHours(weekday=1, start="08:00", end="19:00"),
        WorkingHours(weekday=2, start="08:00", end="19:00"),
        WorkingHours(weekday=3, start="08:00", end="19:00"),
        WorkingHours(weekday=4, start="08:00", end="19:00"),
        WorkingHours(weekday=5, start="08:00", end="13:00"),
    ],
    policies=(
        "Cancelamento gratuito até 24h antes. Chegar 10 minutos antes da consulta. "
        "Procedimentos estéticos (botox, preenchimento) exigem avaliação presencial prévia — "
        "não agendar direto, transbordar para a equipe. Retornos em até 30 dias não são cobrados."
    ),
)
