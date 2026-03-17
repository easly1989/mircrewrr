#!/usr/bin/env python3
"""Entrypoint per il Torznab Proxy multi-sito con admin panel."""

import json
import logging
from importlib import import_module
from pathlib import Path

from config import Config
from torznab.server import TorznabServer
from admin.config_store import ConfigStore
from admin.log_handler import log_handler
from admin.routes import admin_bp, init_admin


def discover_plugins() -> dict:
    """Scopre i plugin disponibili leggendo i manifest.json nelle directory sites/."""
    plugins = {}
    sites_dir = Path(__file__).parent / "sites"

    for manifest_file in sites_dir.glob("*/manifest.json"):
        try:
            with open(manifest_file) as f:
                manifest = json.load(f)
            plugin_id = manifest["id"]
            manifest["_path"] = str(manifest_file.parent)
            plugins[plugin_id] = manifest
        except Exception as e:
            logging.getLogger("main").warning(f"Failed to load plugin manifest {manifest_file}: {e}")

    return plugins


def main():
    config = Config.from_env()
    config.setup_logging()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    # Installa log handler per admin panel
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    logger = logging.getLogger("main")

    # Scopri plugin disponibili
    plugins = discover_plugins()
    logger.info(f"Discovered plugins: {list(plugins.keys())}")

    # Build site registry from plugins
    site_registry = {pid: p["module"] for pid, p in plugins.items()}

    # Config store (persistente su file, fallback env vars)
    config_store = ConfigStore(config.data_dir / "config.json", config)

    server = TorznabServer(api_key=config_store.get_raw().get("api_key", config.api_key))

    # Registra admin panel
    init_admin(server, config_store, site_registry, plugins)
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

        site_type = site_cfg.get("plugin", site_cfg.get("type", site_name))
        if site_type not in site_registry:
            logger.warning(f"Plugin '{site_type}' not found. Available: {list(site_registry.keys())}")
            continue

        module_path = site_registry[site_type]
        try:
            module = import_module(module_path)
            site_config = config_store.build_site_config(site_name)
            site = module.create_site(site_config)
            server.register_site(site_name, site)
        except Exception as e:
            logger.exception(f"Failed to load site '{site_name}': {e}")

    if not server.sites:
        logger.warning("No sites loaded! Use the admin panel at /admin to add sites.")

    logger.info(f"=== Torznab Proxy v7.1.0 starting on {config.host}:{config.port} ===")
    logger.info(f"Admin panel: http://{config.host}:{config.port}/admin")
    logger.info(f"Active sites: {list(server.sites.keys())}")
    logger.info(f"CF Bypass Proxy: {config.cf_bypass_url}")

    server.app.run(host=config.host, port=config.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
