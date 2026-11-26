"""Admin panel Flask Blueprint: REST API + SPA serving."""

import json
import logging
import time
from importlib import import_module

import requests
from flask import Blueprint, jsonify, request, Response, render_template

from . import external
from .log_handler import log_handler

logger = logging.getLogger("admin")

# Blueprint con static e templates relativi a questo modulo
admin_bp = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/admin/static",
)

# Riferimenti impostati da main.py dopo la creazione
_server = None
_config_store = None
_site_registry = None
_plugins = None
_plugin_folders = []
_start_time = time.time()


def init_admin(server, config_store, site_registry, plugins=None, plugin_folders=None):
    """Inizializza i riferimenti globali del modulo admin."""
    global _server, _config_store, _site_registry, _plugins, _plugin_folders
    _server = server
    _config_store = config_store
    _site_registry = site_registry
    _plugins = plugins or {}
    _plugin_folders = plugin_folders or []


# === SPA ===

@admin_bp.route("/admin")
@admin_bp.route("/admin/")
def admin_index():
    return render_template("index.html")


# === STATUS ===

@admin_bp.route("/admin/api/status")
def api_status():
    sites_status = {}
    for name, site in _server.sites.items():
        sites_status[name] = site.health_info()

    return jsonify({
        "version": "7.1.0",
        "uptime_seconds": int(time.time() - _start_time),
        "active_sites": list(_server.sites.keys()),
        "available_plugins": list(_plugins.keys()),
        "plugin_folders": _plugin_folders,
        "sites": sites_status,
    })


# === PLUGINS ===

@admin_bp.route("/admin/api/plugins")
def api_list_plugins():
    """Lista plugin disponibili con i loro manifest."""
    result = {}
    for pid, manifest in _plugins.items():
        # Ritorna manifest senza il path interno
        safe = {k: v for k, v in manifest.items() if not k.startswith("_")}
        result[pid] = safe
    return jsonify(result)


# === CONFIG ===

@admin_bp.route("/admin/api/config")
def api_get_config():
    return jsonify(_config_store.get())


@admin_bp.route("/admin/api/config", methods=["PUT"])
def api_update_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    try:
        _config_store.update(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Applica log level immediatamente
    if "log_level" in data:
        level = logging.DEBUG if data["log_level"] == "DEBUG" else logging.INFO
        logging.getLogger().setLevel(level)
        logger.info(f"Log level changed to {data['log_level']}")

    return jsonify({"ok": True})


# === EXTERNAL INDEXERS ===

def _external_or_404(name):
    entry = _config_store.get_external_indexer(name)
    return entry, (None if entry else (jsonify({"error": "External indexer not found"}), 404))


@admin_bp.route("/admin/api/external/<name>/status")
def api_external_status(name):
    """Status + download queue of an external indexer; unreachable -> 200 with up=false."""
    entry, err = _external_or_404(name)
    return err or jsonify(external.fetch_status(entry))


@admin_bp.route("/admin/api/external/<name>/settings", methods=["GET", "PUT"])
def api_external_settings(name):
    """Read or change an external indexer's settings; applied at its next restart."""
    entry, err = _external_or_404(name)
    if err:
        return err
    body = None
    if request.method == "PUT":
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not all(isinstance(v, str) for v in body.values()):
            return jsonify({"error": "Expected an object of strings"}), 400
    data, code = external.call_settings(entry, request.method, "/api/settings", body)
    return jsonify(data), code


@admin_bp.route("/admin/api/external/<name>/restart", methods=["POST"])
def api_external_restart(name):
    entry, err = _external_or_404(name)
    if err:
        return err
    data, code = external.call_settings(entry, "POST", "/api/restart")
    return jsonify(data), code


@admin_bp.route("/admin/api/external/<name>/logs")
def api_external_logs(name):
    """SSE passthrough of an external indexer's log stream."""
    entry, err = _external_or_404(name)
    if err:
        return err
    try:
        upstream = external.open_logs(entry)
    except requests.RequestException as e:
        return jsonify({"error": external.describe_error(e)}), 502

    def generate():
        try:
            yield from upstream.iter_content(chunk_size=None)
        except requests.RequestException:
            pass  # upstream went away; the browser reconnects
        finally:
            upstream.close()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# === SITES ===

@admin_bp.route("/admin/api/sites")
def api_list_sites():
    sites_config = _config_store.get_sites()
    result = {}
    for name, cfg in sites_config.items():
        # Maschera password
        safe_cfg = cfg.copy()
        if safe_cfg.get("password"):
            safe_cfg["password"] = "••••••••"

        safe_cfg["active"] = name in _server.sites
        if name in _server.sites:
            safe_cfg["health"] = _server.sites[name].health_info()

        result[name] = safe_cfg

    return jsonify(result)


@admin_bp.route("/admin/api/sites/<name>", methods=["PUT"])
def api_update_site(name):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    if not _config_store.update_site(name, data):
        return jsonify({"error": "Site not found"}), 404

    # Ricarica il sito con la nuova config. Il nuovo prende il posto del vecchio solo quando
    # è pronto: se fallisce il vecchio continua a rispondere, senza una finestra di 404.
    site_cfg = _config_store.get_site(name)
    if site_cfg and site_cfg.get("enabled", True):
        error = _activate_site(name)
        if error:
            return jsonify({"ok": True, "warning": f"Saved but reload failed: {error}"})
    elif name in _server.sites:
        _server.unregister_site(name)

    return jsonify({"ok": True})


@admin_bp.route("/admin/api/sites/<name>/retry-login", methods=["POST"])
def api_retry_login(name):
    session = getattr(_server.sites.get(name), "session", None)
    if not hasattr(session, "retry_login"):
        return jsonify({"error": "Site not active or without login"}), 404
    ok = session.retry_login()
    return jsonify({"ok": bool(ok), "login_blocked": session.login_blocked})


def _activate_site(name: str) -> str | None:
    """Attiva un sito. Ritorna None se ok, messaggio errore altrimenti."""
    site_cfg = _config_store.get_site(name)
    if not site_cfg:
        return "Site config not found"

    site_type = site_cfg.get("plugin", site_cfg.get("type", name))
    if site_type not in _site_registry:
        return f"Unknown plugin: {site_type}"

    try:
        module_path = _site_registry[site_type]
        module = import_module(module_path)
        config = _config_store.build_site_config(name)
        site = module.create_site(config)
        _server.register_site(name, site)
        return None
    except Exception as e:
        logger.exception(f"Failed to activate site '{name}': {e}")
        return str(e)


# === LOGS SSE ===

@admin_bp.route("/admin/api/logs")
def api_logs_sse():
    """Server-Sent Events stream per log in tempo reale."""
    def generate():
        q = log_handler.subscribe()
        try:
            # Invia log recenti come batch iniziale
            recent = log_handler.get_recent(100)
            if recent:
                yield f"data: {json.dumps(recent)}\n\n"

            # Stream nuovi log
            while True:
                try:
                    entry = q.get(timeout=30)
                    yield f"data: {json.dumps([entry])}\n\n"
                except Exception:
                    # Timeout: invia keepalive
                    yield ": keepalive\n\n"
        finally:
            log_handler.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
