"""A workspace's own config: the file `workspace.yaml` in its folder.

Read through the store's layout, while the server runs, through a cache keyed
by the file's revision. A file that does not parse or validate is held as
refused, with its reason, so the server can refuse the workspace until an admin
fixes it.
"""

import json
import logging
import os

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow import faults
from kubed.selenium_flow.flows.store import LocalFlowStore
from kubed.selenium_flow.names import InvalidName
from kubed.selenium_flow.workspace import config as config_module
from kubed.selenium_flow.workspace.config import CONFIG_FILE, Configs
from kubed.selenium_flow.workspace.store import MemoryStore, RedisStore, Workspace

from .conftest import TOKEN, log_lines
from .fakes import FakeRedis, seated

pytestmark = pytest.mark.unit

NAME = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def configs(tmp_path):
    return Configs(LocalFlowStore(tmp_path))


def write(tmp_path, text, name=NAME):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    path = folder / CONFIG_FILE
    path.write_text(text, encoding="utf-8")
    return path


# ---- reading -----------------------------------------------------------------


def test_a_workspace_with_no_file_has_no_config(configs):
    loaded = configs.load(NAME)
    assert (loaded.config, loaded.error) == (None, None)
    assert configs.rows(loaded) == {"flows.global": {"value": None, "source": "default"}}


def test_no_data_dir_is_no_config():
    configs = Configs(None)
    assert configs.load(NAME).config is None
    assert configs.load(NAME).error is None
    assert configs.kept(NAME) is False


def test_a_file_sets_its_rows_and_says_so(configs, tmp_path):
    write(tmp_path, "flows:\n  global:\n  - shop,login\n  - admin\n")
    loaded = configs.load(NAME)
    assert loaded.error is None
    assert configs.rows(loaded) == {
        "flows.global": {"value": ["shop,login", "admin"], "source": "workspace"}
    }


def test_a_json_object_reads_too(configs, tmp_path):
    write(tmp_path, json.dumps({"flows": {"global": ["shop"]}}))
    assert configs.rows(configs.load(NAME))["flows.global"]["value"] == ["shop"]


def test_an_empty_file_is_a_config_with_every_default(configs, tmp_path):
    write(tmp_path, "")
    loaded = configs.load(NAME)
    assert loaded.error is None and loaded.config is not None
    assert configs.rows(loaded)["flows.global"]["source"] == "default"


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("colour: blue\n", "colour"),
        ("flows:\n  colour: blue\n", "flows.colour"),
        ("flows:\n  global: [a]\n  password: hunter2\n", "flows.password"),
    ],
)
def test_an_unknown_key_at_any_level_is_refused_naming_it(configs, tmp_path, text, named):
    write(tmp_path, text)
    loaded = configs.load(NAME)
    assert loaded.config is None
    assert f"`{named}`" in loaded.error
    assert "hunter2" not in loaded.error, "a refusal never quotes a value"


@pytest.mark.parametrize(
    "text",
    [
        "- a\n- b\n",
        "just words\n",
        "flows: [a]\n",
        "flows:\n  global: shop\n",
        "flows:\n  global:\n  - ''\n",
        "flows:\n  global:\n  - '   '\n",
        "flows:\n  global:\n  - 3\n",
    ],
)
def test_a_file_of_the_wrong_shape_is_refused(configs, tmp_path, text):
    write(tmp_path, text)
    loaded = configs.load(NAME)
    assert loaded.config is None and loaded.error


def test_yaml_that_does_not_parse_is_refused_with_where_not_what(configs, tmp_path):
    write(tmp_path, "flows:\n  global: [secret-value\n")
    loaded = configs.load(NAME)
    assert loaded.config is None
    assert "line" in loaded.error
    assert "secret-value" not in loaded.error


def test_a_file_that_is_a_link_is_refused_not_followed(configs, tmp_path):
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text("flows:\n  global: [a]\n")
    (tmp_path / NAME).mkdir()
    (tmp_path / NAME / CONFIG_FILE).symlink_to(elsewhere)
    loaded = configs.load(NAME)
    assert loaded.config is None and loaded.error


def test_a_name_that_cannot_be_a_folder_is_refused(configs):
    with pytest.raises(InvalidName):
        configs.load("../escape")


def test_a_row_that_is_not_a_tag_filter_is_refused_naming_it(configs, tmp_path):
    write(tmp_path, "flows:\n  global:\n  - shop\n  - 'Not A Tag!'\n")
    error = configs.load(NAME).error
    assert "`flows.global`" in error and "Not A Tag!" in error


# ---- the cache -----------------------------------------------------------------


def test_a_warm_read_opens_nothing(configs, tmp_path, monkeypatch):
    write(tmp_path, "flows:\n  global: [a]\n")
    first = configs.load(NAME)
    opened = []
    real = config_module.open_regular
    monkeypatch.setattr(
        config_module, "open_regular", lambda path: opened.append(path) or real(path)
    )
    assert configs.load(NAME) is first
    assert opened == []


def test_a_refused_file_is_cached_too(configs, tmp_path, monkeypatch):
    write(tmp_path, "colour: blue\n")
    first = configs.load(NAME)
    monkeypatch.setattr(
        config_module, "open_regular", lambda path: pytest.fail("read again")
    )
    assert configs.load(NAME) is first


def test_an_edit_on_disk_is_seen_at_the_next_read(configs, tmp_path):
    path = write(tmp_path, "flows:\n  global: [a]\n")
    assert configs.load(NAME).error is None
    path.write_text("colour: blue\n")
    assert configs.load(NAME).error
    path.write_text("flows:\n  global: [b]\n")
    assert configs.rows(configs.load(NAME))["flows.global"]["value"] == ["b"]


def test_an_edit_in_the_same_clock_tick_is_seen(configs, tmp_path):
    """Same mtime, different size: the revision carries size and inode beside it."""
    path = write(tmp_path, "flows:\n  global: [a]\n")
    stamp = path.stat().st_mtime_ns
    assert configs.load(NAME).error is None
    path.write_text("colour: blue, green\n")
    os.utime(path, ns=(stamp, stamp))
    assert configs.load(NAME).error


def test_a_removed_file_is_no_config_again(configs, tmp_path):
    path = write(tmp_path, "colour: blue\n")
    assert configs.load(NAME).error
    path.unlink()
    assert configs.load(NAME).error is None


def test_a_refused_file_is_logged_once_per_revision(configs, tmp_path):
    path = write(tmp_path, "colour: blue\n")
    with log_lines(config_module.log.name) as lines:
        configs.load(NAME)
        configs.load(NAME)
        path.write_text("colour: red, and more\n")
        configs.load(NAME)
        configs.load(NAME)
    warnings = [m for level, m, _ in lines if level == logging.WARNING]
    assert len(warnings) == 2
    assert all(NAME in m and "`colour`" in m for m in warnings)


# ---- writing -------------------------------------------------------------------


def test_save_writes_the_text_as_given(configs, tmp_path):
    text = "# offered here\nflows:\n  global:\n  - shop  # the store\n"
    configs.save(NAME, text)
    assert (tmp_path / NAME / CONFIG_FILE).read_text() == text
    assert configs.read_text(NAME) == text
    assert configs.kept(NAME)


def test_save_refuses_text_that_does_not_validate_and_keeps_the_file(configs, tmp_path):
    configs.save(NAME, "flows:\n  global: [a]\n")
    with pytest.raises(ValueError, match="`colour`"):
        configs.save(NAME, "colour: blue\n")
    assert configs.read_text(NAME) == "flows:\n  global: [a]\n"


def test_save_replaces_the_file_whole(configs, tmp_path, monkeypatch):
    """A new file renamed over the old: a reader holding the old inode never
    sees half of the new text."""
    configs.save(NAME, "flows:\n  global: [a]\n")
    before = (tmp_path / NAME / CONFIG_FILE).stat().st_ino
    configs.save(NAME, "flows:\n  global: [b]\n")
    assert (tmp_path / NAME / CONFIG_FILE).stat().st_ino != before
    assert [p.name for p in (tmp_path / NAME).iterdir()] == [CONFIG_FILE]


def test_remove_takes_the_file(configs, tmp_path):
    configs.save(NAME, "flows: {}\n")
    assert configs.remove(NAME) is True
    assert configs.kept(NAME) is False
    assert configs.remove(NAME) is False


def test_validate_answers_the_reason(configs):
    configs.validate("flows:\n  global: [a]\n")
    with pytest.raises(ValueError, match=r"`flows\.nope`"):
        configs.validate("flows:\n  nope: 1\n")


def test_with_no_data_dir_a_write_is_a_conflict():
    configs = Configs(None)
    for write_one in (
        lambda: configs.save(NAME, "flows: {}\n"),
        lambda: configs.remove(NAME),
    ):
        with pytest.raises(faults.Conflict, match=r"data\.dir is unset"):
            write_one()


def test_kept_names_are_the_folders_holding_a_file(configs, tmp_path):
    configs.save(NAME, "flows: {}\n")
    (tmp_path / "laptop").mkdir()
    write(tmp_path, "colour: blue\n", name="broken")
    assert configs.kept_names() == ["broken", NAME]


# ---- kept: a workspace with a config never expires ------------------------------


def _kept_stores(tmp_path, configs):
    memory_now = [1000.0]
    memory = MemoryStore(ttl=60, clock=lambda: memory_now[0])
    fake = FakeRedis()
    redis = RedisStore(fake, prefix="p:", ttl=60)
    for store in (memory, redis):
        store.kept = configs.kept
    return memory, memory_now, redis, fake


def test_a_kept_workspace_is_written_with_no_expiry(configs, tmp_path):
    memory, now, redis, fake = _kept_stores(tmp_path, configs)
    configs.save(NAME, "flows: {}\n")
    memory.set(NAME, Workspace(session_id="a"))
    redis.set(NAME, Workspace(session_id="a"))
    redis.update(NAME, lambda r: r.with_site_data({"x": 1}))
    now[0] += 10**9
    assert memory.get(NAME) is not None
    assert NAME in memory.records()
    assert fake.expiries[f"p:{NAME}"] is None


def test_the_first_write_after_the_file_goes_sets_the_expiry_again(configs, tmp_path):
    memory, now, redis, fake = _kept_stores(tmp_path, configs)
    configs.save(NAME, "flows: {}\n")
    memory.set(NAME, Workspace(session_id="a"))
    redis.set(NAME, Workspace(session_id="a"))
    configs.remove(NAME)
    memory.update(NAME, lambda r: r.with_site_data({"x": 1}))
    redis.upsert(NAME, lambda r: r.with_site_data({"x": 1}))
    assert fake.expiries[f"p:{NAME}"] == 60
    now[0] += 61
    assert memory.get(NAME) is None


def test_an_invalid_config_keeps_the_workspace_too(configs, tmp_path):
    """Kept is the file being there: a broken one is still the admin's."""
    write(tmp_path, "colour: blue\n")
    memory, now, _, _ = _kept_stores(tmp_path, configs)
    memory.set(NAME, Workspace())
    now[0] += 61
    assert memory.get(NAME) is not None


def test_the_server_keeps_a_workspace_its_config_names(data_server, tmp_path):
    """Wired at boot: the store asks the server's configs."""
    data_server.workspaces.configs.save(NAME, "flows: {}\n")
    store = data_server.store
    seated(store, NAME)
    assert store._data[NAME][0] == float("inf")


def test_the_admin_list_shows_a_kept_workspace_with_no_record(data_server):
    data_server.workspaces.configs.save("kept-only", "flows: {}\n")
    seated(data_server.store, NAME)
    client = TestClient(data_server.mcp.server.http_app(), headers=AUTH)
    rows = {r["key"]: r for r in client.get("/admin/workspaces").json()["workspaces"]}
    assert set(rows) == {"kept-only", NAME}
    assert rows["kept-only"]["kept"] is True
    assert rows["kept-only"]["attached"] is False
    assert rows[NAME]["kept"] is False
