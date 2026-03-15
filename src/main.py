#!/usr/bin/env python3
"""Entrypoint per il Torznab Proxy multi-sito con admin panel."""

import sys
import logging
from importlib import import_module

from config import Config
from torznab.server import TorznabServer
from admin.config_store import ConfigStore
from admin.log_handler import log_handler
from admin.routes import admin_bp, init_admin

# Registry dei siti disponibili
SITE_REGISTRY = {
    "mircrew": "sites.mircrew.site",
}


def main():
    config = Config.from_env()
    config.setup_logging()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    # Installa log handler per admin panel
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    logger = logging.getLogger("main")

    # Config store (persistente su file, fallback env vars)
    config_store = ConfigStore(config.data_dir / "config.json", config)

    server = TorznabServer(api_key=config_store.get_raw().get("api_key", config.api_key))

    # Registra admin panel
    init_admin(server, config_store, SITE_REGISTRY)
    server.app.register_blueprint(admin_bp)

    # Carica siti abilitati
    enabled_sites = config_store.get_enabled_sites()
    if not enabled_sites:
        # Fallback a env vars se nessun sito configurato
        enabled_sites = config.enabled_sites

    for site_name in enabled_sites:
        site_cfg = config_store.get_site(site_name)
        if not site_cfg:
            logger.warning(f"Site '{site_name}' not found in config store, skipping")
            continue

        site_type = site_cfg.get("type", site_name)
        if site_type not in SITE_REGISTRY:
            logger.warning(f"Site type '{site_type}' not found in registry. Available: {list(SITE_REGISTRY.keys())}")
            continue

        module_path = SITE_REGISTRY[site_type]
        try:
            module = import_module(module_path)
            site_config = config_store.build_site_config(site_name)
            site = module.create_site(site_config)
            server.register_site(site_name, site)
        except Exception as e:
            logger.exception(f"Failed to load site '{site_name}': {e}")

    if not server.sites:
        logger.warning("No sites loaded! Use the admin panel at /admin to add sites.")

    logger.info(f"=== Torznab Proxy v7.0.0 starting on {config.host}:{config.port} ===")
    logger.info(f"Admin panel: http://{config.host}:{config.port}/admin")
    logger.info(f"Active sites: {list(server.sites.keys())}")
    logger.info(f"Byparr/FlareSolverr: {config.flaresolverr_url}")

    server.app.run(host=config.host, port=config.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
