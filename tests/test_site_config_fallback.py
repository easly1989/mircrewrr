"""A site saved from the panel with blank fields keeps using the env values."""

from admin.config_store import ConfigStore
from config import Config


def store(tmp_path, **env):
    return ConfigStore(tmp_path / "config.json", Config(**env))


ENV = dict(base_url="https://forum.example", username="env-user", password="env-pass")


def test_blank_site_fields_fall_back_to_env(tmp_path):
    s = store(tmp_path, **ENV)
    s.add_site("mircrew", {"enabled": True, "plugin": "mircrew"})
    # What the panel sends when the form shows nothing for an env-fed site.
    s.update_site("mircrew", {"base_url": "", "username": "", "password": "new-pass"})
    cfg = s.build_site_config("mircrew")
    assert (cfg.base_url, cfg.username) == ("https://forum.example", "env-user")
    assert cfg.password == "new-pass"  # a value that was typed still wins


def test_filled_site_fields_override_env(tmp_path):
    s = store(tmp_path, **ENV)
    s.add_site("other", {"plugin": "mircrew", "base_url": "https://other.example", "username": "u", "password": "p"})
    cfg = s.build_site_config("other")
    assert (cfg.base_url, cfg.username, cfg.password) == ("https://other.example", "u", "p")
