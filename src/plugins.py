"""Plugin discovery: built-in plugins in src/sites/, operator plugins in <data dir>/plugins/.

A plugin is a folder with a manifest.json and a module that has `create_site(config)`.
Every folder found is reported with the reason it did not load; one bad plugin never
stops the proxy or the others.
"""

import json
import logging
import re
from importlib import import_module
from pathlib import Path

logger = logging.getLogger("plugins")

PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]*$")

BUILTIN = "built-in"
OPERATOR = "data/plugins"


def _check(folder: Path, source: str, builtin_ids: set) -> tuple[dict | None, str | None]:
    """Load one plugin folder. Returns (manifest, None) or (None, reason)."""
    manifest_file = folder / "manifest.json"
    if not manifest_file.is_file():
        return None, "no manifest.json"
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"manifest.json is not valid JSON: {e}"
    if not isinstance(manifest, dict):
        return None, "manifest.json is not an object"

    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not PLUGIN_ID.match(plugin_id):
        return None, 'manifest "id" must be lowercase letters, digits and underscores, starting with a letter'
    if plugin_id != folder.name:
        return None, f'manifest "id" is "{plugin_id}" but the folder is "{folder.name}"'
    if source == OPERATOR and plugin_id in builtin_ids:
        return None, "the id belongs to a built-in plugin"

    module_path = manifest.get("module")
    if not isinstance(module_path, str) or not module_path.startswith(f"sites.{plugin_id}."):
        return None, f'manifest "module" must start with "sites.{plugin_id}."'
    try:
        module = import_module(module_path)
    except Exception as e:
        return None, f"import of {module_path} failed: {type(e).__name__}: {e}"
    if not callable(getattr(module, "create_site", None)):
        return None, f"{module_path} has no create_site(config)"

    manifest["_path"] = str(folder)
    manifest["_source"] = source
    return manifest, None


def discover_plugins(builtin_dir: Path, operator_dir: Path) -> tuple[dict, list]:
    """Returns ({id: manifest} of the plugins that loaded, [folder report, ...]).

    Each report is {"folder", "source", "id", "name", "loaded", "error"}. The operator
    folder is added to the `sites` package path, so a plugin there is imported as
    `sites.<id>.site`, like a built-in one. A built-in plugin wins an id clash.
    """
    plugins: dict = {}
    report: list = []

    builtin_ids = {p.parent.name for p in builtin_dir.glob("*/manifest.json")}

    if operator_dir.is_dir():
        import sites
        if str(operator_dir) not in sites.__path__:
            sites.__path__.append(str(operator_dir))

    for source, root in ((BUILTIN, builtin_dir), (OPERATOR, operator_dir)):
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
            manifest, error = _check(folder, source, builtin_ids)
            entry = {
                "folder": f"{'src/sites' if source == BUILTIN else 'data/plugins'}/{folder.name}",
                "source": source,
                "id": (manifest or {}).get("id") or folder.name,
                "name": (manifest or {}).get("name") or folder.name,
                "loaded": manifest is not None,
                "error": error,
            }
            report.append(entry)
            if manifest is not None:
                plugins[manifest["id"]] = manifest
            elif source == OPERATOR or error != "no manifest.json":
                logger.warning(f"Plugin {entry['folder']} not loaded: {error}")
    return plugins, report
