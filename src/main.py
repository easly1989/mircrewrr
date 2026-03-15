#!/usr/bin/env python3
"""Entrypoint per il Torznab Proxy multi-sito."""

import sys
import logging
from importlib import import_module

from config import Config
from torznab.server import TorznabServer

logger = logging.getLogger("main")

# Registry dei siti disponibili
SITE_REGISTRY = {
    "mircrew": "sites.mircrew.site",
}


def main():
    config = Config.from_env()
    config.setup_logging()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    if not config.username or not config.password:
        logger.error("MIRCREW_USERNAME and MIRCREW_PASSWORD required!")
        sys.exit(1)

    server = TorznabServer(api_key=config.api_key)

    for site_name in config.enabled_sites:
        if site_name not in SITE_REGISTRY:
            logger.warning(f"Site '{site_name}' not found in registry. Available: {list(SITE_REGISTRY.keys())}")
            continue

        module_path = SITE_REGISTRY[site_name]
        try:
            module = import_module(module_path)
            site = module.create_site(config)
            server.register_site(site_name, site)
        except Exception as e:
            logger.exception(f"Failed to load site '{site_name}': {e}")
            sys.exit(1)

    if not server.sites:
        logger.error("No sites loaded! Check ENABLED_SITES config.")
        sys.exit(1)

    logger.info(f"=== Torznab Proxy v7.0.0 starting on {config.host}:{config.port} ===")
    logger.info(f"Enabled sites: {list(server.sites.keys())}")
    logger.info(f"Byparr/FlareSolverr: {config.flaresolverr_url}")

    server.app.run(host=config.host, port=config.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
