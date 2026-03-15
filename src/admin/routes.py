"""Admin panel Flask Blueprint: REST API + SPA serving."""

import json
import logging
import time
from importlib import import_module
from pathlib import Path

from flask import Blueprint, jsonify, request, Response, render_template

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
_start_time = time.time()


def init_admin(server, config_store, site_registry):
    """Inizializza i riferimenti globali del modulo admin."""
    global _server, _config_store, _site_registry
    _server = server
    _config_store = config_store
    _site_registry = site_registry


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
        "version": "7.0.0",
        "uptime_seconds": int(time.time() - _start_time),
        "active_sites": list(_server.sites.keys()),
        "available_site_types": list(_site_registry.keys()),
        "sites": sites_status,
    })


# === CONFIG ===

@admin_bp.route("/admin/api/config")
def api_get_config():
    return jsonify(_config_store.get())


@admin_bp.route("/admin/api/config", methods=["PUT"])
def api_update_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    _config_store.update(data)

    # Applica log level immediatamente
    if "log_level" in data:
        level = logging.DEBUG if data["log_level"] == "DEBUG" else logging.INFO
        logging.getLogger().setLevel(level)
        logger.info(f"Log level changed to {data['log_level']}")

    return jsonify({"ok": True})


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


@admin_bp.route("/admin/api/sites", methods=["POST"])
def api_add_site():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    name = data.get("name", "").strip()
    site_type = data.get("type", "").strip()

    if not name or not site_type:
        return jsonify({"error": "name and type required"}), 400

    if site_type not in _site_registry:
        return jsonify({"error": f"Unknown site type: {site_type}. Available: {list(_site_registry.keys())}"}), 400

    if _config_store.get_site(name):
        return jsonify({"error": f"Site '{name}' already exists"}), 409

    site_config = {
        "enabled": True,
        "type": site_type,
        "base_url": data.get("base_url", ""),
        "username": data.get("username", ""),
        "password": data.get("password", ""),
    }
    _config_store.add_site(name, site_config)

    # Prova ad attivare il sito
    error = _activate_site(name)
    if error:
        return jsonify({"ok": True, "warning": f"Site saved but failed to activate: {error}"}), 201

    return jsonify({"ok": True}), 201


@admin_bp.route("/admin/api/sites/<name>", methods=["PUT"])
def api_update_site(name):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    if not _config_store.update_site(name, data):
        return jsonify({"error": "Site not found"}), 404

    return jsonify({"ok": True})


@admin_bp.route("/admin/api/sites/<name>", methods=["DELETE"])
def api_delete_site(name):
    if name in _server.sites:
        _server.unregister_site(name)

    if not _config_store.remove_site(name):
        return jsonify({"error": "Site not found"}), 404

    return jsonify({"ok": True})


@admin_bp.route("/admin/api/sites/<name>/toggle", methods=["POST"])
def api_toggle_site(name):
    new_state = _config_store.toggle_site(name)
    if new_state is None:
        return jsonify({"error": "Site not found"}), 404

    if new_state:
        error = _activate_site(name)
        if error:
            return jsonify({"enabled": True, "active": False, "error": error})
        return jsonify({"enabled": True, "active": True})
    else:
        if name in _server.sites:
            _server.unregister_site(name)
        return jsonify({"enabled": False, "active": False})


def _activate_site(name: str) -> str | None:
    """Attiva un sito. Ritorna None se ok, messaggio errore altrimenti."""
    site_cfg = _config_store.get_site(name)
    if not site_cfg:
        return "Site config not found"

    site_type = site_cfg.get("type", name)
    if site_type not in _site_registry:
        return f"Unknown site type: {site_type}"

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
