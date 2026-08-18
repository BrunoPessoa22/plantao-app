"""The AI employee: Claude tool-use loop grounded in the clinic config + slot engine."""
import json
import logging

import anthropic

from . import db, scheduling
from .clinic import ClinicConfig
from .settings import settings

log = logging.getLogger("plantao.brain")

_client: anthropic.AsyncAnthropic | None = None

MAX_TOOL_ROUNDS = 6

KEY_MISSING_REPLY = (
    "Demo aguardando configuração: a chave de IA (ANTHROPIC_API_KEY) ainda não foi "
    "definida no servidor. Assim que for configurada, este funcionário responde de verdade."
)

OVERLOAD_REPLY = (
    "Estamos com um volume alto agora — já registrei sua mensagem e a equipe "
    "vai te responder por aqui em instantes."
)


def _tools(cfg: ClinicConfig) -> list[dict]:
    service_ids = [s.id for s in cfg.services]
    professional_ids = [p.id for p in cfg.professionals]
    return [
        {
            "name": "buscar_horarios",
            "description": (
                "Busca os próximos horários realmente disponíveis na agenda da clínica "
                "para um serviço. Sempre use esta ferramenta antes de oferecer horários — "
                "nunca invente disponibilidade."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string", "enum": service_ids},
                    "professional_id": {"type": "string", "enum": professional_ids,
                                        "description": "Opcional: restringir a um profissional"},
                },
                "required": ["service_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "agendar_consulta",
            "description": (
                "Confirma um agendamento em um horário retornado por buscar_horarios. "
                "Exige nome do paciente e convênio (ou 'particular'). Use somente após o "
                "paciente confirmar explicitamente o horário."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string", "enum": service_ids},
                    "professional_id": {"type": "string", "enum": professional_ids},
                    "inicio_iso": {"type": "string", "description": "ISO datetime exato vindo de buscar_horarios"},
                    "nome_paciente": {"type": "string"},
                    "convenio": {"type": "string", "description": "Nome do convênio ou 'particular'"},
                },
                "required": ["service_id", "professional_id", "inicio_iso", "nome_paciente", "convenio"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "transbordar_humano",
            "description": (
                "Passa a conversa para um atendente humano da clínica. Use para: urgências, "
                "procedimentos que exigem avaliação, reclamações sérias, pedidos fora do escopo, "
                "ou quando o paciente pedir para falar com uma pessoa."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"motivo": {"type": "string"}},
                "required": ["motivo"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "registrar_ocorrencia",
            "description": "Registra uma reclamação ou ocorrência para o dono ver no relatório diário.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["reclamacao", "elogio", "outro"]},
                    "resumo": {"type": "string"},
                },
                "required": ["tipo", "resumo"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]


def _system_stable(cfg: ClinicConfig) -> str:
    services = "\n".join(
        f"- {s.name} (service_id={s.id}): R${s.price_particular} particular; "
        f"convênios aceitos: {', '.join(s.convenios) or 'nenhum'}"
        for s in cfg.services
    )
    pros = "\n".join(
        f"- {p.name}, {p.title} (professional_id={p.id}): atende {', '.join(p.services)}"
        for p in cfg.professionals
    )
    return f"""Você é a recepção da {cfg.name} no WhatsApp — um funcionário de IA do Plantão.

CLÍNICA
Endereço: {cfg.address} · Telefone: {cfg.phone_display}
Convênios da clínica: {', '.join(cfg.convenios)}

SERVIÇOS
{services}

PROFISSIONAIS
{pros}

POLÍTICAS
{cfg.policies}

REGRAS INEGOCIÁVEIS
1. Nunca invente horário, preço ou convênio: horários vêm SEMPRE de buscar_horarios; preços e convênios vêm SOMENTE deste cadastro.
2. Você não é médico: nunca dê orientação clínica, diagnóstico ou conduta. Sintomas graves ou urgência (dor forte, falta de ar, sangramento, etc.): oriente procurar pronto-socorro ou ligar 192 (SAMU) e use transbordar_humano.
3. Antes de agendar: confirme serviço, convênio (ou particular) e nome do paciente. Só chame agendar_consulta depois que o paciente confirmar o horário explicitamente.
4. Se o convênio do paciente não for aceito para o serviço, diga com clareza e ofereça o valor particular.
5. Pedidos fora do escopo (resultados de exame, receitas, procedimentos estéticos, negociação de preço): transbordar_humano.
6. LGPD: peça apenas o necessário (nome; convênio). Não peça CPF, endereço ou dados sensíveis.

ESTILO
Português do Brasil, tom WhatsApp: curto, caloroso e direto. No máximo 3 frases por resposta, sem listas longas. Ofereça no máximo 3 horários por vez. Nunca use emojis."""


async def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


def _run_tool(cfg: ClinicConfig, conversation_id: int, contact: str, name: str, args: dict) -> dict:
    if name == "buscar_horarios":
        slots = scheduling.find_slots(cfg, args["service_id"], args.get("professional_id"))
        return {"horarios": slots} if slots else {"horarios": [], "aviso": "sem_horarios_nos_proximos_14_dias"}
    if name == "agendar_consulta":
        result = scheduling.book(
            cfg, args["service_id"], args["professional_id"], args["inicio_iso"],
            args["nome_paciente"], contact, args["convenio"],
        )
        if result.get("ok"):
            db.add_event(conversation_id, "agendamento", result)
        return result
    if name == "transbordar_humano":
        db.mark_handed_off(conversation_id)
        db.add_event(conversation_id, "transbordo", {"motivo": args.get("motivo", "")})
        return {"ok": True, "mensagem": "Conversa marcada para atendimento humano."}
    if name == "registrar_ocorrencia":
        kind = "reclamacao" if args.get("tipo") == "reclamacao" else "ocorrencia"
        db.add_event(conversation_id, kind, args)
        return {"ok": True}
    return {"ok": False, "erro": f"ferramenta_desconhecida:{name}"}


async def reply(cfg: ClinicConfig, conversation_id: int, contact: str, user_text: str) -> str:
    """One full turn: user message in → assistant text out (tools resolved internally)."""
    db.add_message(conversation_id, "user", user_text)
    if not settings.anthropic_key_present:
        db.add_message(conversation_id, "assistant", KEY_MISSING_REPLY)
        return KEY_MISSING_REPLY

    client = await _get_client()
    messages: list[dict] = db.get_history(conversation_id)
    system = [
        {"type": "text", "text": _system_stable(cfg), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Agora: {scheduling.fmt_pt(scheduling.now_local())}, {scheduling.now_local().strftime('%d/%m/%Y')} (America/Sao_Paulo)."},
    ]
    tools = _tools(cfg)

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.beta.messages.create(
                model=settings.model,
                max_tokens=2048,
                system=system,
                messages=messages,
                tools=tools,
                output_config={"effort": settings.effort},
                betas=["server-side-fallback-2026-06-01"],
                fallbacks=[{"model": settings.fallback_model}],
            )
            if response.stop_reason == "refusal":
                text = "Não consigo ajudar com isso por aqui — vou passar para a equipe da clínica."
                db.mark_handed_off(conversation_id)
                db.add_event(conversation_id, "transbordo", {"motivo": "refusal"})
                db.add_message(conversation_id, "assistant", text)
                return text
            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                if not text:
                    text = "Desculpa, não entendi — pode repetir?"
                db.add_message(conversation_id, "assistant", text)
                return text

            # Execute every tool call in this assistant turn; return all results in ONE user msg
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                args = block.input if isinstance(block.input, dict) else json.loads(block.input)
                out = _run_tool(cfg, conversation_id, contact, block.name, args)
                results.append({
                    "type": "tool_result", "tool_use_id": block.id,
                    "content": json.dumps(out, ensure_ascii=False),
                })
            messages.append({"role": "user", "content": results})
        text = "Vou pedir para a equipe continuar por aqui — só um instante."
        db.mark_handed_off(conversation_id)
        db.add_message(conversation_id, "assistant", text)
        return text
    except anthropic.AuthenticationError:
        log.error("ANTHROPIC_API_KEY inválida")
        db.add_message(conversation_id, "assistant", KEY_MISSING_REPLY)
        return KEY_MISSING_REPLY
    except anthropic.RateLimitError:
        log.warning("rate limited")
        db.add_message(conversation_id, "assistant", OVERLOAD_REPLY)
        return OVERLOAD_REPLY
    except anthropic.APIStatusError as exc:
        log.error("API error %s: %s", exc.status_code, exc.message)
        db.add_message(conversation_id, "assistant", OVERLOAD_REPLY)
        return OVERLOAD_REPLY
    except anthropic.APIConnectionError:
        log.error("connection error to Anthropic API")
        db.add_message(conversation_id, "assistant", OVERLOAD_REPLY)
        return OVERLOAD_REPLY
