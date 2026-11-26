"""The sections registered with kubed-mcp: serving's names and defaults, the root's
keys, and the store that naming Redis asks for."""

import re

import kubed.mcp
import pytest
from kubed.mcp import Source

from kubed.selenium_flow.config import (
    SECTION_CLASSES,
    RedisSettings,
    ServingSettings,
    WorkspaceSettings,
    derive_store,
)

from .conftest import configured, loaded, quick

pytestmark = pytest.mark.unit


def test_the_serving_section_keeps_this_server_s_defaults():
    serving = configured().serving
    assert isinstance(serving, ServingSettings)
    assert (serving.transport, serving.host, serving.port, serving.log_level) == (
        "http", "0.0.0.0", 8000, "INFO")


def test_serving_keeps_its_names():
    cfg, sources = loaded(["--port", "9000"],
                          {"HOST": "127.0.0.1", "TRANSPORT": "stdio", "LOG_LEVEL": "debug"})
    assert (cfg.serving.transport, cfg.serving.host, cfg.serving.port, cfg.serving.log_level) == (
        "stdio", "127.0.0.1", 9000, "DEBUG")
    assert sources["serving.port"] == Source("arg", "--port")
    assert sources["serving.host"] == Source("env", "HOST")


@pytest.mark.parametrize("environ, where", [
    ({"TRANSPORT": "sse"}, "serving.transport (env TRANSPORT)"),
    ({"PORT": "0"}, "serving.port (env PORT)"),
])
def test_serving_refuses_what_this_server_does_not_offer(environ, where):
    with pytest.raises(ValueError, match=re.escape(where)):
        loaded(environ=environ)


@pytest.mark.parametrize("key", ["transport", "host", "port", "log_level"])
def test_a_top_level_serving_key_in_the_file_is_refused_naming_it(tmp_path, key):
    path = tmp_path / "c.yaml"
    path.write_text(f"{key}: x\n")
    with pytest.raises(ValueError, match=re.escape(f"{key} (file {path}): Extra inputs are not permitted")):
        loaded(["--config-file", str(path)])


def test_the_root_keys_stay_at_the_top():
    cfg, sources = loaded(environ={"ROUTE_PREFIX": "/flow", "LINK_TTL": "120",
                                   "PUBLIC_BASE_URL": "https://flow.example.com"})
    assert (cfg.route_prefix, cfg.link_ttl, cfg.public_base_url) == (
        "/flow", 120, "https://flow.example.com")
    assert sources["route_prefix"] == Source("env", "ROUTE_PREFIX")


def test_every_section_is_registered_under_its_key():
    cfg = configured()
    assert list(SECTION_CLASSES) == [
        "auth", "oidc", "grid", "workspace", "session", "redis", "data", "recording", "capture",
        "live", "secrets", "mcp", "security", "serving", "telemetry", "delivery"]
    for key, cls in SECTION_CLASSES.items():
        assert isinstance(getattr(cfg, key), cls), key
    assert SECTION_CLASSES["telemetry"] is kubed.mcp.TelemetrySettings
    assert SECTION_CLASSES["delivery"] is kubed.mcp.DeliverySettings


@pytest.mark.parametrize("environ, store", [
    ({}, "memory"),
    ({"REDIS_HOST": "redis.example.com"}, "redis"),
    ({"REDIS_URL": "redis://redis.example.com:6379/0"}, "redis"),
    ({"REDIS_HOST": "redis.example.com", "WORKSPACE_STORE": "memory"}, "memory"),
    ({"REDIS_URL": "  "}, "memory"),
    ({"REDIS_PORT": "6380"}, "memory"),
])
def test_naming_redis_asks_for_the_redis_store(environ, store):
    cfg, sources = loaded(environ=environ)
    assert cfg.workspace.store == store
    if "WORKSPACE_STORE" not in environ:
        assert sources["workspace.store"] == Source("default")


def test_the_store_follows_redis_given_in_code():
    assert configured(redis=RedisSettings(host="r.example.com")).workspace.store == "redis"
    assert configured(redis={"url": "redis://r.example.com"}).workspace.store == "redis"
    explicit = configured(redis={"host": "r.example.com"}, workspace=WorkspaceSettings(store="memory"))
    assert explicit.workspace.store == "memory"


def test_the_store_derivation_copies_an_instance_and_is_idempotent():
    workspace = WorkspaceSettings(ttl=42)
    data = {"redis": RedisSettings(host="r.example.com"), "workspace": workspace}
    once = derive_store(data)
    assert derive_store(once) == once
    assert once["workspace"].store == "redis" and once["workspace"].ttl == 42
    assert workspace.store == "memory" and data["workspace"] is workspace
    assert set(once) == set(data)
    assert derive_store({"redis": {}}) == {"redis": {}}
    assert derive_store("not a mapping") == "not a mapping"


def test_the_store_derivation_adds_only_the_workspace_key_and_its_result_validates():
    derived = derive_store({"redis": {"url": "redis://r.example.com"}})
    assert set(derived) == {"redis", "workspace"}
    assert quick(config=derived).config.workspace.store == "redis"


@pytest.mark.parametrize("redis", [
    {"url": " "}, {"host": "\t"}, {"url": None}, RedisSettings(host=" "),
    RedisSettings(url=" "),
])
def test_a_blank_redis_address_in_code_is_unset(redis):
    assert configured(redis=redis).workspace.store == "memory"
