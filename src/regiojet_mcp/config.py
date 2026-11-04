import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://brn-ybus-pubapi.sa.cz/restapi"


@dataclass(frozen=True)
class Settings:
    base_url: str = DEFAULT_BASE_URL
    username: str | None = None
    password: str | None = None
    token: str | None = None  # customer account token (Bearer), enables booking tools
    lang: str = "en"
    currency: str = "EUR"
    timeout: float = 20.0
    cache_ttl: float = 24 * 3600
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()  # .env in the working directory; real env vars take precedence
        return cls(
            base_url=os.getenv("REGIOJET_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            username=os.getenv("REGIOJET_USER") or None,
            password=os.getenv("REGIOJET_PASSWORD") or None,
            token=os.getenv("REGIOJET_API_KEY") or None,
            lang=os.getenv("REGIOJET_LANG", "en"),
            currency=os.getenv("REGIOJET_CURRENCY", "EUR"),
            timeout=float(os.getenv("REGIOJET_TIMEOUT", "20")),
        )
