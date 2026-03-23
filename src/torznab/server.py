"""Server Torznab generico multi-sito."""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from flask import Flask, request, Response, jsonify

from .models import TorznabResult

logger = logging.getLogger("torznab")


class BaseSite(ABC):
    """Interfaccia che ogni sito deve implementare."""

    @abstractmethod
    def search(self, query: str, categories: Optional[List[int]],
               target_season: Optional[int], target_episode: Optional[int]) -> List[TorznabResult]:
        """Ricerca contenuti. Ritorna lista di TorznabResult."""

    @abstractmethod
    def download(self, topic_id: str, infohash: Optional[str],
                 season: Optional[int], episode: Optional[int]) -> Optional[str]:
        """Ritorna magnet URI o None."""

    @abstractmethod
    def get_capabilities_xml(self) -> str:
        """Ritorna XML capabilities per questo sito."""

    @abstractmethod
    def health_info(self) -> dict:
        """Ritorna info di stato per health check."""

    def parse_season_from_query(self, query: str) -> Optional[int]:
        """Estrae stagione dalla query. Può essere sovrascritta."""
        return None

    def parse_episode_from_query(self, query: str) -> Optional[int]:
        """Estrae episodio dalla query. Può essere sovrascritta."""
        return None


class TorznabServer:
    """Server Torznab generico che gestisce più siti."""

    def __init__(self, api_key: str = ""):
        self.app = Flask(__name__)
        self.api_key = api_key
        self.sites: Dict[str, BaseSite] = {}
        self._register_global_routes()

    def register_site(self, name: str, site: BaseSite):
        """Registra un sito su /{name}/api e /{name}/download."""
        self.sites[name] = site

        # Usa closure con default arg per catturare il valore corretto
        self.app.add_url_rule(
            f"/{name}/api",
            f"{name}_api",
            lambda n=name: self._handle_api(n),
            methods=["GET"],
        )
        self.app.add_url_rule(
            f"/{name}/download",
            f"{name}_download",
            lambda n=name: self._handle_download(n),
            methods=["GET"],
        )
        # Debug endpoints
        self.app.add_url_rule(
            f"/{name}/thread/<topic_id>",
            f"{name}_thread",
            lambda topic_id, n=name: self._handle_thread_debug(n, topic_id),
            methods=["GET"],
        )
        self.app.add_url_rule(
            f"/{name}/debug-search",
            f"{name}_debug_search",
            lambda n=name: self._handle_debug_search(n),
            methods=["GET"],
        )

        logger.info(f"Site '{name}' registered at /{name}/api")

    def unregister_site(self, name: str):
        """Rimuove un sito e le sue routes."""
        if name not in self.sites:
            return

        del self.sites[name]

        # Rimuovi le rules dal URL map
        rules_to_remove = [
            rule for rule in self.app.url_map.iter_rules()
            if rule.endpoint in (f"{name}_api", f"{name}_download", f"{name}_thread", f"{name}_debug_search")
        ]
        for rule in rules_to_remove:
            self.app.url_map._rules.remove(rule)
            if rule.endpoint in self.app.url_map._rules_by_endpoint:
                del self.app.url_map._rules_by_endpoint[rule.endpoint]
            if rule.endpoint in self.app.view_functions:
                del self.app.view_functions[rule.endpoint]

        # Forza rebuild del URL map adapter
        self.app.url_map.update()

        logger.info(f"Site '{name}' unregistered")

    def _register_global_routes(self):
        @self.app.route("/")
        def index():
            return jsonify({
                "status": "ok",
                "service": "Torznab Proxy",
                "version": "7.1.0",
                "sites": list(self.sites.keys()),
            })

        @self.app.route("/health")
        def health():
            sites_health = {}
            for name, site in self.sites.items():
                sites_health[name] = site.health_info()
            return jsonify({
                "status": "ok",
                "version": "7.1.0",
                "sites": sites_health,
            })

    def _check_api_key(self):
        """Verifica API key. Ritorna Response di errore o None se OK."""
        if self.api_key and request.args.get("apikey") != self.api_key:
            return Response(
                '<?xml version="1.0"?><error code="100" description="Invalid API Key"/>',
                mimetype="application/xml", status=401,
            )
        return None

    def _handle_api(self, site_name: str):
        """Dispatch caps/search per il sito specifico."""
        err = self._check_api_key()
        if err:
            return err

        site = self.sites[site_name]
        t = request.args.get("t", "caps")

        if t == "caps":
            return Response(site.get_capabilities_xml(), mimetype="application/xml")

        if t in ["search", "tvsearch", "movie", "music", "book"]:
            return self._do_search(site, site_name)

        return Response(
            f'<?xml version="1.0"?><error code="203" description="Unknown: {t}"/>',
            mimetype="application/xml", status=400,
        )

    def _do_search(self, site: BaseSite, site_name: str):
        """Gestisce ricerca Torznab."""
        query = request.args.get("q", "")
        cat_str = request.args.get("cat", "")

        # Parse season/episode
        target_season = None
        target_episode = None

        season_param = request.args.get("season")
        ep_param = request.args.get("ep")

        if season_param:
            try:
                target_season = int(season_param)
            except (ValueError, TypeError):
                pass
        if ep_param:
            try:
                target_episode = int(ep_param)
            except (ValueError, TypeError):
                pass

        # Fallback: parse from query
        if target_season is None:
            target_season = site.parse_season_from_query(query)
        if target_episode is None:
            target_episode = site.parse_episode_from_query(query)

        # Parse categories
        categories = None
        if cat_str:
            categories = [int(c) for c in cat_str.split(",") if c.isdigit()]

        results = site.search(query, categories, target_season, target_episode)

        # Genera XML
        download_base = f"http://{request.host}/{site_name}/download"
        items = ""
        for r in results:
            items += r.to_xml_item(download_base)

        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:torznab="http://torznab.com/schemas/2015/feed">
<channel>
<title>{site_name}</title>
<link>{request.host_url}</link>
{items}
</channel>
</rss>'''

        return Response(xml, mimetype="application/rss+xml")

    def _handle_download(self, site_name: str):
        """Gestisce download per il sito specifico."""
        site = self.sites[site_name]

        topic_id = request.args.get("topic_id")
        infohash = request.args.get("infohash", "").upper() or None

        season_param = request.args.get("season")
        ep_param = request.args.get("ep")

        target_season = None
        target_episode = None

        if season_param:
            try:
                target_season = int(season_param)
            except (ValueError, TypeError):
                pass
        if ep_param:
            try:
                target_episode = int(ep_param)
            except (ValueError, TypeError):
                pass

        if not topic_id:
            return "Missing topic_id", 400

        logger.info(f"DOWNLOAD [{site_name}]: topic={topic_id}, infohash={infohash or 'N/A'}, "
                    f"S{target_season}E{target_episode}")

        magnet = site.download(topic_id, infohash, target_season, target_episode)

        if not magnet:
            return "Magnet not found", 404

        return Response(status=302, headers={"Location": magnet})

    def _handle_thread_debug(self, site_name: str, topic_id: str):
        """Debug endpoint per ispezionare un thread."""
        site = self.sites[site_name]
        if hasattr(site, "debug_thread"):
            return jsonify(site.debug_thread(topic_id))
        return jsonify({"error": "debug not supported"})

    def _handle_debug_search(self, site_name: str):
        """Debug endpoint: esegue ricerca e ritorna diagnostica JSON."""
        site = self.sites[site_name]
        query = request.args.get("q", "")
        cat_str = request.args.get("cat", "")

        categories = None
        if cat_str:
            categories = [int(c) for c in cat_str.split(",") if c.isdigit()]

        results = site.search(query, categories, None, None)

        # Import parser for scoring (only MIRCrew sites have it)
        scored_results = []
        for r in results:
            item = {
                "title": r.title,
                "link": r.link,
                "guid": r.guid,
                "category": r.category,
                "pub_date": r.pub_date,
                "size": r.size,
            }
            if hasattr(r, "episode_info") and r.episode_info:
                item["episode_info"] = r.episode_info
            scored_results.append(item)

        return jsonify({
            "query": query,
            "result_count": len(results),
            "results": scored_results,
        })
