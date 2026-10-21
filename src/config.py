"""Configurazione centralizzata da variabili d'ambiente."""

import ipaddress
import os
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any


_INDEXER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def normalize_external_indexers(raw) -> List[Dict[str, str]]:
    """Validate a list of {name, url, api_key} and return clean copies.

    The name ends up in /admin/api/external/<name>/..., hence the strict pattern.
    Raises ValueError with a message meant for the admin user.
    """
    if not isinstance(raw, list):
        raise ValueError("external_indexers must be a list")
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each external indexer must be an object")
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip().rstrip("/")
        if not _INDEXER_NAME.match(name):
            raise ValueError(f"Invalid external indexer name: {name!r} (letters, digits, - and _ only)")
        if name.lower() in seen:
            raise ValueError(f"Duplicate external indexer name: {name}")
        if not re.match(r"^https?://[^/\s]+", url):
            raise ValueError(f"External indexer '{name}' needs an http(s) URL")
        seen.add(name.lower())
        result.append({"name": name, "url": url, "api_key": str(item.get("api_key", "") or "")})
    return result


def parse_networks(spec: str, var: str) -> list:
    """Parse "192.168.1.0/24,100.114.0.0/16" (a bare IP is a /32 or /128).

    Empty means none. A malformed entry raises ValueError, so a typo stops the start
    instead of silently leaving the admin panel open or closed.
    """
    nets = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            raise ValueError(f"{var}: {part!r} is not an IP address or network")
    return nets


def parse_addresses(spec: str, var: str) -> list:
    """Parse "172.22.0.1,fd00::1": single addresses only, never networks."""
    addrs = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            addrs.append(ipaddress.ip_address(part))
        except ValueError:
            raise ValueError(f"{var}: {part!r} is not a single IP address")
    return addrs


def parse_admin_trust(networks: str, proxies: str, secret: str):
    """ADMIN_TRUSTED_* as (networks, proxies, secret); raises ValueError when inconsistent.

    X-Forwarded-For is believed only from a trusted proxy that also sends the shared
    secret: behind Docker's port proxy every peer looks like the gateway, so the peer
    address alone proves nothing.
    """
    nets = parse_networks(networks, "ADMIN_TRUSTED_NETWORKS")
    addrs = parse_addresses(proxies, "ADMIN_TRUSTED_PROXIES")
    secret = secret.strip()
    if addrs and len(secret) < 16:
        raise ValueError("ADMIN_TRUSTED_PROXIES needs ADMIN_TRUSTED_PROXY_SECRET (16+ characters)")
    for n in nets:
        for a in addrs:
            # Every request from the proxy would be trusted, secret or not.
            if a.version == n.version and a in n:
                raise ValueError(f"ADMIN_TRUSTED_NETWORKS: {n} contains the trusted proxy {a}")
    return nets, addrs, secret


def parse_external_indexers(spec: str, api_key: str = "") -> List[Dict[str, str]]:
    """Parse EXTERNAL_INDEXERS ("name=url,name2=url2"); bad entries are skipped."""
    entries = []
    for part in spec.split(","):
        name, _, url = part.strip().partition("=")
        if name and url:
            entries.append({"name": name, "url": url, "api_key": api_key})
    try:
        return normalize_external_indexers(entries)
    except ValueError as e:
        logging.getLogger("config").warning(f"Ignoring EXTERNAL_INDEXERS: {e}")
        return []


@dataclass
class Config:
    """Configurazione dell'applicazione, caricata da variabili d'ambiente."""

    # Siti abilitati
    enabled_sites: List[str] = field(default_factory=lambda: ["mircrew"])

    # Credenziali sito (usate dal sito attivo)
    base_url: str = ""
    username: str = ""
    password: str = field(default="", repr=False)
    api_key: str = field(default="mircrew-api-key", repr=False)

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

    # External indexers shown in the admin panel: [{name, url, api_key}]
    external_indexers: List[Dict[str, str]] = field(default_factory=list)

    # Admin panel without login for these client networks; the client IP is read from
    # X-Forwarded-For only when the request comes from one of the trusted proxies.
    admin_trusted_networks: list = field(default_factory=list)
    admin_trusted_proxies: list = field(default_factory=list)
    admin_trusted_proxy_secret: str = field(default="", repr=False)

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

        trusted_nets, trusted_proxies, proxy_secret = parse_admin_trust(
            os.getenv("ADMIN_TRUSTED_NETWORKS", ""),
            os.getenv("ADMIN_TRUSTED_PROXIES", ""),
            os.getenv("ADMIN_TRUSTED_PROXY_SECRET", ""),
        )
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
            external_indexers=parse_external_indexers(
                os.getenv("EXTERNAL_INDEXERS", ""), os.getenv("EXTERNAL_INDEXERS_API_KEY", "")
            ),
            admin_trusted_networks=trusted_nets,
            admin_trusted_proxies=trusted_proxies,
            admin_trusted_proxy_secret=proxy_secret,
        )

    def setup_logging(self):
        """Configura il logging globale."""
        level = logging.DEBUG if self.log_level == "DEBUG" else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
