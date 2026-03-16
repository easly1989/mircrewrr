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
_plugins = None
_start_time = time.time()


def init_admin(server, config_store, site_registry, plugins=None):
    """Inizializza i riferimenti globali del modulo admin."""
    global _server, _config_store, _site_registry, _plugins
    _server = server
    _config_store = config_store
    _site_registry = site_registry
    _plugins = plugins or {}


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


@admin_bp.route("/admin/api/plugins/<plugin_id>/files/<path:file_path>")
def api_read_plugin_file(plugin_id, file_path):
    """Legge un file dal plugin."""
    manifest = _plugins.get(plugin_id)
    if not manifest:
        return jsonify({"error": "Plugin not found"}), 404

    # Verifica che il file sia nella lista editable_files
    editable = [f["path"] for f in manifest.get("editable_files", [])]
    if file_path not in editable:
        return jsonify({"error": "File not editable"}), 403

    plugin_dir = Path(manifest["_path"])
    full_path = plugin_dir / file_path

    if not full_path.exists():
        return jsonify({"error": "File not found"}), 404

    # Security: ensure path doesn't escape plugin directory
    try:
        full_path.resolve().relative_to(plugin_dir.resolve())
    except ValueError:
        return jsonify({"error": "Invalid path"}), 403

    try:
        content = full_path.read_text(encoding="utf-8")
        return jsonify({"content": content, "path": file_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/admin/api/plugins/<plugin_id>/files/<path:file_path>", methods=["PUT"])
def api_write_plugin_file(plugin_id, file_path):
    """Scrive un file nel plugin."""
    manifest = _plugins.get(plugin_id)
    if not manifest:
        return jsonify({"error": "Plugin not found"}), 404

    editable = [f["path"] for f in manifest.get("editable_files", [])]
    if file_path not in editable:
        return jsonify({"error": "File not editable"}), 403

    plugin_dir = Path(manifest["_path"])
    full_path = plugin_dir / file_path

    # Security: ensure path doesn't escape plugin directory
    try:
        full_path.resolve().relative_to(plugin_dir.resolve())
    except ValueError:
        return jsonify({"error": "Invalid path"}), 403

    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": "No content provided"}), 400

    try:
        full_path.write_text(data["content"], encoding="utf-8")
        logger.info(f"Plugin file written: {plugin_id}/{file_path}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    plugin = data.get("plugin", "").strip()

    if not name or not plugin:
        return jsonify({"error": "name and plugin required"}), 400

    if plugin not in _site_registry:
        return jsonify({"error": f"Unknown plugin: {plugin}. Available: {list(_site_registry.keys())}"}), 400

    if _config_store.get_site(name):
        return jsonify({"error": f"Site '{name}' already exists"}), 409

    site_config = {
        "enabled": True,
        "plugin": plugin,
        "base_url": data.get("base_url", ""),
        "username": data.get("username", ""),
        "password": data.get("password", ""),
    }

    # Store custom config if provided
    if "custom" in data:
        site_config["custom"] = data["custom"]
    else:
        # Initialize with plugin defaults
        manifest = _plugins.get(plugin, {})
        defaults = {}
        for key, schema in manifest.get("custom_config", {}).items():
            if "default" in schema:
                defaults[key] = schema["default"]
        if defaults:
            site_config["custom"] = defaults

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

    # Se il sito è attivo, ricaricalo con la nuova config
    if name in _server.sites:
        site_cfg = _config_store.get_site(name)
        if site_cfg and site_cfg.get("enabled", True):
            _server.unregister_site(name)
            error = _activate_site(name)
            if error:
                return jsonify({"ok": True, "warning": f"Saved but reactivation failed: {error}"})

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
