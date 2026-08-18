"""Environment-driven settings for the Plantão MVP."""
import os
import pathlib


class Settings:
    def __init__(self) -> None:
        self.model: str = os.environ.get("PLANTAO_MODEL", "claude-opus-5")
        self.fallback_model: str = os.environ.get("PLANTAO_FALLBACK_MODEL", "claude-opus-4-8")
        self.effort: str = os.environ.get("PLANTAO_EFFORT", "low")
        self.admin_token: str = os.environ.get("ADMIN_TOKEN", "plantao-admin-demo")
        self.data_dir: pathlib.Path = pathlib.Path(os.environ.get("DATA_DIR", "./data"))
        # WhatsApp Cloud API (official WABA) — adapter activates only when all three are set
        self.wa_token: str = os.environ.get("WA_TOKEN", "")
        self.wa_phone_number_id: str = os.environ.get("WA_PHONE_NUMBER_ID", "")
        self.wa_verify_token: str = os.environ.get("WA_VERIFY_TOKEN", "plantao-verify")
        self.anthropic_key_present: bool = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.wa_token and self.wa_phone_number_id)


settings = Settings()
