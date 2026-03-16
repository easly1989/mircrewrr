"""Persistenza configurazione su file JSON con fallback a env vars."""

import json
import logging
from pathlib import Path
from typing import Any

from config import Config

logger = logging.getLogger("admin.config")


class ConfigStore:
    """Config persistente su file JSON, con fallback a env vars."""

    def __init__(self, config_file: Path, env_config: Config):
        self.config_file = config_file
        self.env_config = env_config
        self._data = self._load()
        self._migrate()

    def _load(self) -> dict:
        """Carica config da file. Se non esiste, genera da env vars."""
        if self.config_file.exists():
            try:
                with open(self.config_file) as f:
                    data = json.load(f)
                logger.info(f"Config loaded from {self.config_file}")
                return data
            except Exception as e:
                logger.warning(f"Failed to load config file: {e}")

        # Genera config iniziale da env vars
        return self._from_env()

    def _migrate(self):
        """Migra config da versioni precedenti."""
        changed = False

        # Migra flaresolverr_url → cf_bypass_url
        if "flaresolverr_url" in self._data and "cf_bypass_url" not in self._data:
            self._data["cf_bypass_url"] = self._data.pop("flaresolverr_url")
            changed = True
        if "flaresolverr_timeout" in self._data and "cf_bypass_timeout" not in self._data:
            self._data["cf_bypass_timeout"] = self._data.pop("flaresolverr_timeout")
            changed = True

        # Migra siti: rimuovi "type" obsoleto, aggiungi "plugin"
        for name, site_cfg in self._data.get("sites", {}).items():
            if "type" in site_cfg and "plugin" not in site_cfg:
                site_cfg["plugin"] = site_cfg.pop("type")
                changed = True

        if changed:
            self._save()
            logger.info("Config migrated to new format")

    def _from_env(self) -> dict:
        """Genera config dict dalle env vars correnti."""
        c = self.env_config
        return {
            "api_key": c.api_key,
            "cf_bypass_url": c.cf_bypass_url,
            "cf_bypass_timeout": c.cf_bypass_timeout,
            "log_level": c.log_level,
            "sites": {
                "mircrew": {
                    "enabled": "mircrew" in c.enabled_sites,
                    "plugin": "mircrew",
                    "base_url": c.base_url,
                    "username": c.username,
                    "password": c.password,
                }
            }
        }

    def _save(self):
        """Salva config su file."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w") as f:
                json.dump(self._data, f, indent=2)
            logger.info("Config saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def get(self) -> dict:
        """Ritorna config completa (senza password in chiaro)."""
        safe = json.loads(json.dumps(self._data))
        for site in safe.get("sites", {}).values():
            if site.get("password"):
                site["password"] = "••••••••"
        return safe

    def get_raw(self) -> dict:
        """Ritorna config completa (con password)."""
        return self._data.copy()

    def update(self, changes: dict):
        """Aggiorna configurazione globale."""
        for key in ["api_key", "cf_bypass_url", "cf_bypass_timeout", "log_level"]:
            if key in changes:
                self._data[key] = changes[key]
        self._save()

    def get_sites(self) -> dict:
        """Ritorna configurazione siti."""
        return self._data.get("sites", {})

    def get_site(self, name: str) -> dict | None:
        """Ritorna configurazione di un sito specifico."""
        return self._data.get("sites", {}).get(name)

    def add_site(self, name: str, site_config: dict):
        """Aggiunge un sito."""
        if "sites" not in self._data:
            self._data["sites"] = {}
        self._data["sites"][name] = site_config
        self._save()

    def update_site(self, name: str, changes: dict):
        """Aggiorna configurazione di un sito."""
        site = self._data.get("sites", {}).get(name)
        if not site:
            return False
        for key, value in changes.items():
            if key == "password" and value == "••••••••":
                continue  # Non sovrascrivere con il placeholder
            site[key] = value
        self._save()
        return True

    def remove_site(self, name: str) -> bool:
        """Rimuove un sito."""
        if name in self._data.get("sites", {}):
            del self._data["sites"][name]
            self._save()
            return True
        return False

    def toggle_site(self, name: str) -> bool | None:
        """Abilita/disabilita un sito. Ritorna il nuovo stato."""
        site = self._data.get("sites", {}).get(name)
        if not site:
            return None
        site["enabled"] = not site.get("enabled", True)
        self._save()
        return site["enabled"]

    def get_enabled_sites(self) -> list[str]:
        """Ritorna lista nomi siti abilitati."""
        return [name for name, cfg in self._data.get("sites", {}).items()
                if cfg.get("enabled", True)]

    def build_site_config(self, name: str) -> Config | None:
        """Costruisce un Config per un sito specifico."""
        site = self.get_site(name)
        if not site:
            return None

        from dataclasses import replace
        return replace(
            self.env_config,
            base_url=site.get("base_url", self.env_config.base_url),
            username=site.get("username", self.env_config.username),
            password=site.get("password", self.env_config.password),
            api_key=self._data.get("api_key", self.env_config.api_key),
            cf_bypass_url=self._data.get("cf_bypass_url", self.env_config.cf_bypass_url),
            cf_bypass_timeout=self._data.get("cf_bypass_timeout", self.env_config.cf_bypass_timeout),
            log_level=self._data.get("log_level", self.env_config.log_level),
            custom=site.get("custom", {}),
        )
