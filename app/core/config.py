from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: Literal["development", "staging", "production"] = "development"
    app_secret_key: str = "dev-secret-key-change-in-production"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = True
    app_name: str = "GaiaPulse"
    app_version: str = "0.1.0"

    # Database
    database_url: str = "postgresql://gaiapulse:gaiapulse@localhost:5432/gaiapulse"

    # Authentication
    session_cookie_name: str = "gaiapulse_session"
    session_max_age_seconds: int = 604800  # 7 days

    # NLP / AI
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # Speech-to-Text
    stt_provider: Literal["whisper", "none"] = "none"
    stt_api_key: str = ""

    # Background jobs
    enable_background_jobs: bool = True

    #: La franja en la que la app no genera notificaciones, en horas locales
    #: enteras. Cruza la medianoche cuando el inicio es mayor que el fin (22 → 8),
    #: y con los dos valores iguales queda vacía. Ver `app/core/clock.py`.
    #:
    #: Acá un valor fuera de rango tumba el arranque, a diferencia de `timezone`,
    #: que se degrada a UTC: la timezone se lee en cada render y no vale tirar
    #: todas las páginas por un typo, pero esto se lee solo en los jobs, y un
    #: `QUIET_HOURS_START=25` silencioso dejaría el horario de silencio apagado
    #: sin que nadie se enterara.
    quiet_hours_start: int = Field(default=22, ge=0, le=23)
    quiet_hours_end: int = Field(default=8, ge=0, le=23)

    # Localization
    timezone: str = "America/Argentina/Buenos_Aires"
    default_locale: str = "es_AR"

    @field_validator("app_secret_key")
    @classmethod
    def validate_secret_key(cls, v: str, info: object) -> str:
        # Warn in non-dev environments but don't fail startup
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def nlp_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def stt_enabled(self) -> bool:
        return self.stt_provider != "none" and bool(
            self.stt_api_key or self.openai_api_key
        )

    @property
    def effective_stt_key(self) -> str:
        return self.stt_api_key or self.openai_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
