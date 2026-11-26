"""Plugin discovery (built-in and data/plugins) and the panel's site reload."""

import json
import shutil
import sys
import types
from importlib import import_module
from pathlib import Path

import pytest

from admin.auth import init_auth
from admin.config_store import ConfigStore
from admin.routes import admin_bp, init_admin
from config import Config
from plugins import discover_plugins
from torznab.server import TorznabServer

BUILTIN_DIR = Path(__file__).parent.parent / "src" / "sites"

SITE_PY = "def create_site(config):\n    return object()\n"


def write_plugin(root, pid, *, manifest=None, site=SITE_PY, folder=None):
    folder = root / (folder or pid)
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    (folder / "site.py").write_text(site)
    if manifest is None:
        manifest = {"id": pid, "name": pid.title(), "module": f"sites.{pid}.site"}
    if manifest is not False:
        (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def by_folder(report):
    return {r["folder"]: r for r in report}


def test_built_in_plugin_is_found(tmp_path):
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path / "none")
    assert "mircrew" in plugins
    assert by_folder(report)["src/sites/mircrew"]["loaded"] is True


def test_operator_plugin_loads_from_data_folder(tmp_path):
    write_plugin(tmp_path, "opdemo_ok")
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    assert "opdemo_ok" in plugins and "mircrew" in plugins
    entry = by_folder(report)["data/plugins/opdemo_ok"]
    assert entry["loaded"] is True and entry["error"] is None and entry["source"] == "data/plugins"
    assert plugins["opdemo_ok"]["module"] == "sites.opdemo_ok.site"


@pytest.mark.parametrize("name, kwargs, reason", [
    ("opdemo_nomanifest", {"manifest": False}, "no manifest.json"),
    ("opdemo_nomodule", {"manifest": {"id": "opdemo_nomodule", "name": "x"}}, 'manifest "module"'),
    ("opdemo_wrongid", {"manifest": {"id": "other", "module": "sites.other.site"}}, 'but the folder is "opdemo_wrongid"'),
    ("opdemo_badimport", {"site": "raise RuntimeError('boom')\n"}, "import of sites.opdemo_badimport.site failed"),
    ("opdemo_nofactory", {"site": "x = 1\n"}, "no create_site"),
])
def test_broken_plugin_is_reported_and_does_not_stop_the_others(tmp_path, name, kwargs, reason):
    write_plugin(tmp_path, name, **kwargs)
    write_plugin(tmp_path, "opdemo_fine")
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    entry = by_folder(report)[f"data/plugins/{name}"]
    assert entry["loaded"] is False and reason in entry["error"]
    assert name not in plugins
    assert "opdemo_fine" in plugins and "mircrew" in plugins


def test_documented_template_plugin_loads_and_runs(tmp_path):
    shutil.copytree(Path(__file__).parent.parent / "docs" / "plugin-template" / "example", tmp_path / "example")
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    assert by_folder(report)["data/plugins/example"]["loaded"] is True
    module = import_module(plugins["example"]["module"])
    site = module.create_site(Config(data_dir=tmp_path, api_key="k", base_url="https://example.org"))
    assert site.search("x", None, None, None) == []
    assert site.health_info()["status"] == "ok" and "<caps>" in site.get_capabilities_xml()


def test_built_in_id_cannot_be_taken_by_an_operator_plugin(tmp_path):
    write_plugin(tmp_path, "mircrew", site="raise RuntimeError('must not run')\n")
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    entry = by_folder(report)["data/plugins/mircrew"]
    assert entry["loaded"] is False and "built-in" in entry["error"]
    assert plugins["mircrew"]["_source"] == "built-in"


@pytest.mark.parametrize("name, patch, reason", [
    ("opdemo_cc_list", {"custom_config": []}, '"custom_config" must be an object of objects'),
    ("opdemo_cs_flat", {"config_schema": {"base_url": "https://x"}}, '"config_schema" must be an object of objects'),
    ("opdemo_name_num", {"name": 5}, '"name" must be text'),
])
def test_manifest_shape_is_checked(tmp_path, name, patch, reason):
    manifest = {"id": name, "name": "x", "module": f"sites.{name}.site", **patch}
    write_plugin(tmp_path, name, manifest=manifest)
    _, report = discover_plugins(BUILTIN_DIR, tmp_path)
    entry = by_folder(report)[f"data/plugins/{name}"]
    assert entry["loaded"] is False and reason in entry["error"]


@pytest.mark.parametrize("pid", ["admin", "static", "health"])
def test_ids_the_proxy_uses_are_reserved(tmp_path, pid):
    write_plugin(tmp_path, pid)
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    assert pid not in plugins and "used by the proxy" in by_folder(report)[f"data/plugins/{pid}"]["error"]


def test_invalid_json_is_reported(tmp_path):
    folder = write_plugin(tmp_path, "opdemo_badjson")
    (folder / "manifest.json").write_text("{not json")
    _, report = discover_plugins(BUILTIN_DIR, tmp_path)
    assert "not valid JSON" in by_folder(report)["data/plugins/opdemo_badjson"]["error"]


# --- the panel reloads a site when its settings are saved ---

class FakeSite:
    def __init__(self, tag):
        self.tag = tag

    def search(self, *a):
        return []

    def download(self, *a):
        return None

    def get_capabilities_xml(self):
        return "<caps/>"

    def health_info(self):
        return {"status": "ok"}


@pytest.fixture
def panel(tmp_path):
    state = {"calls": 0, "fail": False}
    module = types.ModuleType("sites.reload_demo.site")

    def create_site(config):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("login failed")
        return FakeSite(state["calls"])

    module.create_site = create_site
    sys.modules["sites.reload_demo.site"] = module

    config = Config(data_dir=tmp_path, api_key="k")
    store = ConfigStore(tmp_path / "config.json", config)
    store.add_site("demo", {"enabled": True, "plugin": "reload_demo", "base_url": "https://a.example"})
    server = TorznabServer(api_key="k")
    init_auth(server.app, server, tmp_path)
    init_admin(server, store, {"reload_demo": "sites.reload_demo.site"}, {}, [])
    server.app.register_blueprint(admin_bp)
    server.register_site("demo", FakeSite(0))
    client = server.app.test_client()
    client.environ_base["HTTP_ORIGIN"] = "http://localhost"
    assert client.post("/admin/login", data={"api_key": "k"}).status_code == 302
    yield client, server, state
    sys.modules.pop("sites.reload_demo.site", None)


def test_saving_a_site_replaces_it_without_a_gap(panel):
    client, server, state = panel
    r = client.put("/admin/api/sites/demo", json={"base_url": "https://b.example"})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert server.sites["demo"].tag == 1


def test_failed_reload_keeps_the_running_site_and_the_old_settings(panel):
    client, server, state = panel
    old = server.sites["demo"]
    state["fail"] = True
    r = client.put("/admin/api/sites/demo", json={"username": "wrong"})
    assert r.status_code == 400 and "Not saved" in r.get_json()["error"]
    assert server.sites["demo"] is old
    assert client.get("/admin/api/sites").get_json()["demo"].get("username", "") == ""


def test_site_update_takes_only_the_panel_fields(panel):
    client, server, state = panel
    r = client.put("/admin/api/sites/demo", json={"enabled": False, "plugin": "mircrew", "base_url": "https://c.example"})
    assert r.status_code == 200
    site = client.get("/admin/api/sites").get_json()["demo"]
    assert site["enabled"] is True and site["plugin"] == "reload_demo" and site["base_url"] == "https://c.example"
    assert "demo" in server.sites


@pytest.mark.parametrize("body", [["x"], {"custom": "text"}, {"username": 5}, {}])
def test_site_update_rejects_a_bad_body(panel, body):
    client, server, state = panel
    assert client.put("/admin/api/sites/demo", json=body).status_code == 400


def test_custom_keys_the_panel_cannot_edit_survive_a_save(panel):
    client, server, state = panel
    client.put("/admin/api/sites/demo", json={"custom": {"mode": "fast"}})
    client.put("/admin/api/sites/demo", json={"custom": {"category_map": {"1": 2000}}})
    custom = client.get("/admin/api/sites").get_json()["demo"]["custom"]
    assert custom == {"mode": "fast", "category_map": {"1": 2000}}


def test_saving_a_site_that_failed_at_startup_starts_it(panel):
    client, server, state = panel
    server.unregister_site("demo")
    r = client.put("/admin/api/sites/demo", json={"password": "fixed"})
    assert r.get_json() == {"ok": True} and "demo" in server.sites


def make_store(tmp_path, **env):
    return ConfigStore(tmp_path / "config.json", Config(data_dir=tmp_path, api_key="k", **env))


def test_new_plugin_gets_a_site_once(tmp_path):
    store = make_store(tmp_path)
    manifest = {"id": "nyaa", "config_schema": {"base_url": {"default": "https://nyaa.example"}},
                "custom_config": {"category_map": {"default": {"1": 2000}}, "no_default": {}}}
    plugins = {"nyaa": manifest, "mircrew": {"id": "mircrew"}}
    assert store.seed_plugin_sites(plugins) == ["nyaa"]  # mircrew already has its site
    site = store.get_site("nyaa")
    assert site == {"enabled": True, "plugin": "nyaa", "base_url": "https://nyaa.example",
                    "username": "", "password": "", "custom": {"category_map": {"1": 2000}}}
    assert store.seed_plugin_sites(plugins) == []  # nothing new on the next start
    assert "nyaa" in store.get_enabled_sites()


def test_a_site_removed_by_hand_is_not_brought_back(tmp_path):
    store = make_store(tmp_path)
    plugins = {"nyaa": {"id": "nyaa"}, "mircrew": {"id": "mircrew"}}
    store.seed_plugin_sites(plugins)
    del store._data["sites"]["nyaa"]
    del store._data["sites"]["mircrew"]
    store._save()
    again = make_store(tmp_path)
    assert again.seed_plugin_sites(plugins) == [] and again.get_sites() == {}


def test_a_config_from_before_the_seed_does_not_get_mircrew_back(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"api_key": "k", "sites": {}, "external_indexers": []}))
    store = make_store(tmp_path)
    assert store.seed_plugin_sites({"mircrew": {"id": "mircrew"}}) == []
    assert store.get_sites() == {}


def test_a_malformed_manifest_never_stops_the_seed(tmp_path):
    store = make_store(tmp_path)
    plugins = {"bad": {"id": "bad", "config_schema": {"base_url": "https://x"}}, "good": {"id": "good"}}
    assert store.seed_plugin_sites(plugins) == ["good"]


def test_env_credentials_go_to_mircrew_only(tmp_path):
    store = make_store(tmp_path, base_url="https://forum.example", username="env-user", password="env-pass")
    store.add_site("other", {"enabled": True, "plugin": "nyaa", "base_url": "", "username": "", "password": ""})
    other = store.build_site_config("other")
    assert (other.base_url, other.username, other.password) == ("", "", "")
    store.add_site("mine", {"enabled": True, "plugin": "mircrew", "base_url": "", "username": "", "password": ""})
    mine = store.build_site_config("mine")
    assert (mine.base_url, mine.username, mine.password) == ("https://forum.example", "env-user", "env-pass")


def test_status_lists_plugin_folders(tmp_path):
    config = Config(data_dir=tmp_path, api_key="k")
    server = TorznabServer(api_key="k")
    init_auth(server.app, server, tmp_path)
    folders = [{"folder": "data/plugins/x", "loaded": False, "error": "no manifest.json"}]
    init_admin(server, ConfigStore(tmp_path / "config.json", config), {}, {}, folders)
    server.app.register_blueprint(admin_bp)
    c = server.app.test_client()
    c.environ_base["HTTP_ORIGIN"] = "http://localhost"
    c.post("/admin/login", data={"api_key": "k"})
    assert c.get("/admin/api/status").get_json()["plugin_folders"] == folders
