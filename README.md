# Plantão — MVP

Funcionário de IA para PMEs no WhatsApp. Este MVP implementa o loop completo para a
vertical de clínicas: paciente escreve → IA responde com dados reais do cadastro →
consulta horários reais → agenda → confirma → transborda pro humano quando precisa →
dono vê o relatório.

- **Demo web (chat estilo WhatsApp):** `/` — clínica fictícia "Clínica Vida", agenda real
- **Relatório do dono:** `/admin` (exige `ADMIN_TOKEN`)
- **Canal WhatsApp oficial (WABA / Meta Cloud API):** webhook em `/webhook/whatsapp` —
  mesmo cérebro, ativa quando as envs `WA_TOKEN` + `WA_PHONE_NUMBER_ID` forem definidas
- **Health:** `/healthz`

## Arquitetura

```
app/clinic.py      — cadastro da clínica (o "contrato" do funcionário; 1 por cliente)
app/scheduling.py  — motor de agenda determinístico (slots = horário de funcionamento − ocupados)
app/brain.py       — loop de tool-use do Claude (claude-opus-5, fallback server-side)
                     ferramentas: buscar_horarios · agendar_consulta · transbordar_humano · registrar_ocorrencia
app/whatsapp.py    — adaptador Meta Cloud API (WABA oficial)
app/db.py          — SQLite: conversas, mensagens, agendamentos, eventos
app/main.py        — FastAPI: /api/chat, /webhook/whatsapp, /api/admin/overview, /api/digest
static/            — chat demo + painel do dono
```

Regras que o cérebro NUNCA quebra (system prompt): não inventa horário/preço/convênio
(horários só via ferramenta), não dá orientação médica (urgência → 192 + transbordo),
só agenda após confirmação explícita, LGPD-mínimo (só nome + convênio).

## Env vars

| Var | Obrigatória | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | sim (sem ela o chat responde "aguardando configuração") | — |
| `ADMIN_TOKEN` | sim (sem ela: token aleatório gerado no boot, /admin fica inacessível) | — |
| `PLANTAO_MODEL` | não | `claude-opus-5` |
| `PLANTAO_FALLBACK_MODEL` | não | `claude-opus-4-8` |
| `PLANTAO_EFFORT` | não | `high` |
| `WA_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET` | só para o canal WABA (`WA_APP_SECRET` valida a assinatura do webhook) | — |
| `DATA_DIR` | não | `/data` (container) |

Admin auth: `Authorization: Bearer <ADMIN_TOKEN>` (nunca query string). O digest fecha o dia
no fuso da clínica (America/Sao_Paulo).

## Rodar local

```bash
pip install -r requirements.txt
ANTHROPIC_API_KEY=... uvicorn app.main:app --reload
# http://localhost:8000
```

## Ativar o canal WhatsApp oficial (quando o WABA existir)

1. Meta Business: criar app WhatsApp, obter `WA_TOKEN` (system user token) e `WA_PHONE_NUMber_ID`.
2. Configurar webhook: URL `https://<host>/webhook/whatsapp`, verify token = `WA_VERIFY_TOKEN`.
3. Definir as 3 envs e redeploy. O mesmo cérebro passa a atender o número oficial.

## Limitações conscientes do MVP

- SQLite efêmero no container (redeploy zera dados) — anexar volume persistente no Coolify quando virar piloto real.
- Uma clínica hard-coded (`DEMO_CLINIC`) — multi-tenant é a etapa seguinte (config por cliente no DB).
- Sem integração Google Agenda/iClinic ainda — o motor de agenda é próprio; conectores são o próximo passo do onboarding real.
- Sem rate limiting no `/api/chat` público (demo) — adicionar limite por IP antes de divulgar o link amplamente.
- Histórico persiste só o texto das mensagens (contexto de ferramentas vive dentro do turno) — suficiente porque o motor de agenda é determinístico; persistir `content_json` completo é melhoria futura.
- Deploy single-worker (SQLite + locks in-process). Não escalar réplicas sem trocar a persistência.
