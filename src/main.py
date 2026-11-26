#!/usr/bin/env python3
"""Entrypoint per il Torznab Proxy multi-sito con admin panel."""

import logging
from importlib import import_module
from pathlib import Path

from waitress import serve

from config import Config
from plugins import discover_plugins
from torznab.server import TorznabServer
from admin.config_store import ConfigStore
from admin.auth import init_auth
from admin.log_handler import log_handler
from admin.routes import admin_bp, init_admin

# threads: each open log stream (SSE) holds one, hence more than the default 4.
# clear_untrusted_proxy_headers=False: admin.auth reads X-Forwarded-For itself, only
# with the proxy secret. Never set waitress' trusted_proxy: it would rewrite
# REMOTE_ADDR from X-Forwarded-For without checking the secret.
WAITRESS_OPTIONS = {"threads": 16, "clear_untrusted_proxy_headers": False}


def main():
    config = Config.from_env()
    config.setup_logging()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    # Installa log handler per admin panel
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    logger = logging.getLogger("main")

    # Plugin built-in (src/sites/) e dell'operatore (<data dir>/plugins/)
    plugins, plugin_folders = discover_plugins(Path(__file__).parent / "sites", config.data_dir / "plugins")
    logger.info(f"Discovered plugins: {list(plugins.keys())}")

    # Build site registry from plugins
    site_registry = {pid: p["module"] for pid, p in plugins.items()}

    # Config store (persistente su file, fallback env vars)
    config_store = ConfigStore(config.data_dir / "config.json", config)
    config_store.seed_plugin_sites(plugins)

    server = TorznabServer(api_key=config_store.get_raw().get("api_key", config.api_key))

    # Registra admin panel
    init_auth(server.app, server, config.data_dir, config.admin_trusted_networks,
              config.admin_trusted_proxies, config.admin_trusted_proxy_secret)
    init_admin(server, config_store, site_registry, plugins, plugin_folders)
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
        logger.warning("No sites loaded! Check the plugins in the admin panel at /admin, under Indexers.")

    logger.info(f"=== Torznab Proxy v7.1.0 starting on {config.host}:{config.port} ===")
    logger.info(f"Admin panel: http://{config.host}:{config.port}/admin")
    logger.info(f"Active sites: {list(server.sites.keys())}")
    logger.info(f"CF Bypass Proxy: {config.cf_bypass_url}")

    serve(server.app, host=config.host, port=config.port, **WAITRESS_OPTIONS)


if __name__ == "__main__":
    main()
