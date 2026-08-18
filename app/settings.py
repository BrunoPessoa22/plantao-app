"""Environment-driven settings for the Plantão MVP."""
import logging
import os
import pathlib
import secrets

log = logging.getLogger("plantao.settings")

VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class Settings:
    def __init__(self) -> None:
        self.model: str = os.environ.get("PLANTAO_MODEL", "claude-opus-5")
        self.fallback_model: str = os.environ.get("PLANTAO_FALLBACK_MODEL", "claude-opus-4-8")
        effort = os.environ.get("PLANTAO_EFFORT", "high")
        self.effort: str = effort if effort in VALID_EFFORTS else "high"
        admin = os.environ.get("ADMIN_TOKEN", "")
        if not admin:
            # fail closed: random token nobody knows, with a loud log, instead of a guessable default
            admin = secrets.token_hex(16)
            log.warning("ADMIN_TOKEN not set — generated a random token; /admin is unreachable until it is configured")
        self.admin_token: str = admin
        self.data_dir: pathlib.Path = pathlib.Path(os.environ.get("DATA_DIR", "./data"))
        # WhatsApp Cloud API (official WABA) — adapter activates only when token + phone id are set
        self.wa_token: str = os.environ.get("WA_TOKEN", "")
        self.wa_phone_number_id: str = os.environ.get("WA_PHONE_NUMBER_ID", "")
        self.wa_verify_token: str = os.environ.get("WA_VERIFY_TOKEN", "plantao-verify")
        self.wa_app_secret: str = os.environ.get("WA_APP_SECRET", "")
        self.anthropic_key_present: bool = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.wa_token and self.wa_phone_number_id)


settings = Settings()
