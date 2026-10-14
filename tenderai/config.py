"""Runtime settings. Secrets come from the environment / `.env` only and are never logged."""
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Customers in scope (from the assignment).
TARGET_BINS: tuple[str, ...] = (
    "000740001307", "020240002363", "020440003656", "030440003698",
    "050740004819", "051040005150", "100140011059", "120940001946", "140340016539",
    "150540000186", "171041003124", "210240019348", "210240033968", "210941010761",
    "230740013340", "231040023028", "780140000023", "900640000128", "940740000911",
    "940940000384", "960440000220", "970940001378", "971040001050", "980440001034",
    "981140001551", "990340005977", "990740002243",
)
TARGET_YEARS: tuple[int, ...] = (2024, 2025, 2026)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ows_token: SecretStr
    ows_base_url: str = "https://ows.goszakup.gov.kz"
    ows_concurrency: int = 4
    ows_page_size: int = 200  # server-side maximum for GraphQL

    database_url: str = "postgresql://tenderai:tenderai@localhost:5434/tenderai"
    agent_database_url: str = "postgresql://tenderai_agent:tenderai_agent@localhost:5434/tenderai"

    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3.5:9b"
    llm_api_key: SecretStr = SecretStr("local")
    # Layers to put on the GPU. Ollama's auto-fit keeps one Qwen3.5-9B layer in system RAM on an 8 GB card
    # (12% CPU / 88% GPU); forcing all of them onto the RTX 5070 is ~25% faster and fits (6.4 GB used).
    llm_num_gpu: int = 99


@lru_cache
def get_settings() -> Settings:
    return Settings()
