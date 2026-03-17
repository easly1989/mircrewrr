"""Configurazione centralizzata da variabili d'ambiente."""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any


@dataclass
class Config:
    """Configurazione dell'applicazione, caricata da variabili d'ambiente."""

    # Siti abilitati
    enabled_sites: List[str] = field(default_factory=lambda: ["mircrew"])

    # Credenziali sito (usate dal sito attivo)
    base_url: str = ""
    username: str = ""
    password: str = ""
    api_key: str = "mircrew-api-key"

    # Server
    host: str = "0.0.0.0"
    port: int = 9696

    # Storage
    data_dir: Path = field(default_factory=lambda: Path("/app/data"))

    # Cloudflare Bypass Proxy (Byparr/FlareSolverr)
    cf_bypass_url: str = "http://localhost:8191"
    cf_bypass_timeout: int = 60000

    # Logging
    log_level: str = "INFO"

    # Plugin-specific custom config
    custom: Dict[str, Any] = field(default_factory=dict)

    # Legacy aliases for backward compatibility
    @property
    def flaresolverr_url(self) -> str:
        return self.cf_bypass_url

    @property
    def flaresolverr_timeout(self) -> int:
        return self.cf_bypass_timeout

    @classmethod
    def from_env(cls) -> "Config":
        """Crea configurazione dalle variabili d'ambiente."""
        enabled = os.getenv("ENABLED_SITES", "mircrew")
        enabled_sites = [s.strip() for s in enabled.split(",") if s.strip()]

        return cls(
            enabled_sites=enabled_sites,
            base_url=os.getenv("MIRCREW_URL", "https://mircrew-releases.org"),
            username=os.getenv("MIRCREW_USERNAME", ""),
            password=os.getenv("MIRCREW_PASSWORD", ""),
            api_key=os.getenv("MIRCREW_API_KEY", "mircrew-api-key"),
            host=os.getenv("PROXY_HOST", "0.0.0.0"),
            port=int(os.getenv("PROXY_PORT", "9696")),
            data_dir=Path(os.getenv("DATA_DIR", "/app/data")),
            cf_bypass_url=os.getenv("FLARESOLVERR_URL", "http://localhost:8191"),
            cf_bypass_timeout=int(os.getenv("FLARESOLVERR_TIMEOUT", "60000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def setup_logging(self):
        """Configura il logging globale."""
        level = logging.DEBUG if self.log_level == "DEBUG" else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
