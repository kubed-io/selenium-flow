"""The secrets catalogue: what it publishes, and what it never does.

Two things are being protected. A value must not reach a caller through any
surface — there is no tool that returns one, which is asserted rather than
assumed. And the reader has to cope with a real Kubernetes mount, which is a
directory of symlinks through a timestamped `..data` directory rather than the
plain tree it looks like from outside.
"""

import pytest

from kubed.selenium_flow import secrets
from kubed.selenium_flow.secrets import (
    ALLOWED_URLS,
    DESCRIPTION,
    Catalogue,
    FilesystemSource,
    origin,
)

pytestmark = pytest.mark.unit


def make_secret(root, name, **keys):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for key, value in keys.items():
        (directory / key).write_text(value)
    return directory


@pytest.fixture
def source(tmp_path):
    make_secret(tmp_path, "nextcloud-admin", username="admin", password="hunter2")
    return FilesystemSource(tmp_path)


# ---- reading a directory of files --------------------------------------------


def test_a_directory_is_a_secret_and_a_file_is_a_key(source):
    entry = source.entry("nextcloud-admin")
    assert entry["name"] == "nextcloud-admin"
    assert entry["keys"] == ["password", "username"]


def test_a_value_is_read_only_when_it_is_asked_for(source):
    assert source.value("nextcloud-admin", "password") == "hunter2"


def test_no_published_entry_carries_a_value(source):
    """The whole point. If this ever fails, the feature is off."""
    assert "hunter2" not in str(source.entry("nextcloud-admin"))


def test_a_trailing_newline_is_not_part_of_the_password(tmp_path):
    """`echo secret > file` is how these get made by hand, and a stray newline
    typed into a login form fails in a way nobody would guess."""
    make_secret(tmp_path, "app", token="abc123\n")
    assert FilesystemSource(tmp_path).value("app", "token") == "abc123"


def test_a_missing_secret_and_a_missing_key_are_both_none(source):
    assert source.entry("nope") is None
    assert source.value("nope", "password") is None
    assert source.value("nextcloud-admin", "nope") is None


def test_only_one_level_deep(tmp_path):
    """A Kubernetes mount is exactly one level; recursing would invent a shape
    nothing else produces."""
    directory = make_secret(tmp_path, "app", token="t")
    (directory / "nested").mkdir()
    (directory / "nested" / "deep").write_text("x")
    assert FilesystemSource(tmp_path).entry("app")["keys"] == ["token"]


# ---- a real Kubernetes mount --------------------------------------------------


def test_it_reads_the_shape_kubernetes_actually_mounts(tmp_path):
    """Not the tree it looks like. A projected volume is a timestamped
    directory, a `..data` symlink to it, and one symlink per key through that —
    verified against this pod's own service account mount.
    """
    root = tmp_path / "secrets"
    mount = root / "nextcloud-admin"
    stamped = mount / "..2026_09_11_00_00_00.123456789"
    stamped.mkdir(parents=True)
    (stamped / "username").write_text("admin")
    (stamped / "password").write_text("hunter2")
    (mount / "..data").symlink_to(stamped, target_is_directory=True)
    (mount / "username").symlink_to(mount / "..data" / "username")
    (mount / "password").symlink_to(mount / "..data" / "password")

    source = FilesystemSource(root)
    assert source.names() == ["nextcloud-admin"]
    entry = source.entry("nextcloud-admin")
    # The machinery is not keys, and the timestamped directory is not a secret.
    assert entry["keys"] == ["password", "username"]
    assert source.value("nextcloud-admin", "password") == "hunter2"


def test_the_dotted_machinery_is_never_listed_as_a_secret(tmp_path):
    root = tmp_path
    (root / "..2026_09_11").mkdir()
    (root / "..data").symlink_to(root / "..2026_09_11", target_is_directory=True)
    make_secret(root, "real", token="t")
    assert FilesystemSource(root).names() == ["real"]


# ---- metadata, which is not a key ---------------------------------------------


def test_the_reserved_keys_describe_the_secret_without_being_keys(tmp_path):
    make_secret(
        tmp_path,
        "nextcloud-admin",
        password="hunter2",
        **{
            DESCRIPTION: "Admin login for the homelab Nextcloud",
            ALLOWED_URLS: "https://nextcloud.example.com\nhttps://other.example.com\n",
        },
    )
    entry = FilesystemSource(tmp_path).entry("nextcloud-admin")
    assert entry["keys"] == ["password"]
    assert entry["description"] == "Admin login for the homelab Nextcloud"
    assert entry["allowed_urls"] == [
        "https://nextcloud.example.com",
        "https://other.example.com",
    ]


def test_a_reserved_key_cannot_be_bound(tmp_path):
    """So a bind cannot read its own leash."""
    make_secret(tmp_path, "app", password="p", **{ALLOWED_URLS: "https://x.test"})
    assert FilesystemSource(tmp_path).value("app", ALLOWED_URLS) is None


# ---- several directories ------------------------------------------------------


def test_the_first_directory_wins_like_path(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    make_secret(first, "shared", token="from-first")
    make_secret(second, "shared", token="from-second")
    make_secret(second, "only-in-second", token="t")
    catalogue = Catalogue([FilesystemSource(first), FilesystemSource(second)])
    assert [s["name"] for s in catalogue.listing()["secrets"]] == [
        "only-in-second",
        "shared",
    ]
    assert catalogue.value("shared", "token") == "from-first"


def test_an_entry_says_where_it_came_from(tmp_path):
    """"Why am I getting the wrong password" is otherwise unanswerable."""
    make_secret(tmp_path, "app", token="t")
    entry = Catalogue([FilesystemSource(tmp_path)]).entry("app")
    assert entry["source"] == "filesystem"
    assert entry["location"] == str(tmp_path)


def test_a_directory_that_does_not_exist_is_not_an_error(tmp_path):
    """An operator naming a mount that is not there should get no secrets, not
    a server that will not start."""
    catalogue = Catalogue([FilesystemSource(tmp_path / "nope")])
    assert catalogue.listing()["secrets"] == []


# ---- the catalogue is cached, values are not ----------------------------------


def test_the_catalogue_is_cached_but_a_value_is_read_every_time(tmp_path):
    """A rotated credential must never be served from memory after it stopped
    being valid."""
    make_secret(tmp_path, "app", token="first")
    clock = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    catalogue = Catalogue([FilesystemSource(tmp_path)], ttl=100,
                          clock=lambda: next(clock))
    assert catalogue.listing()["count"] == 1
    make_secret(tmp_path, "added-later", token="t")
    # Still cached.
    assert catalogue.listing()["count"] == 1
    # But the value is not.
    make_secret(tmp_path, "app", token="rotated")
    assert catalogue.value("app", "token") == "rotated"


def test_the_cache_expires(tmp_path):
    make_secret(tmp_path, "app", token="t")
    now = [0.0]
    catalogue = Catalogue([FilesystemSource(tmp_path)], ttl=30, clock=lambda: now[0])
    assert catalogue.listing()["count"] == 1
    make_secret(tmp_path, "later", token="t")
    now[0] = 31.0
    assert catalogue.listing()["count"] == 2


# ---- allowed URLs, matched by origin ------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://nextcloud.example.com/login", True),
        ("https://nextcloud.example.com:443/x", False),  # a different origin
        ("http://nextcloud.example.com/login", False),  # scheme matters
        # The attack the substring check lets through.
        ("https://nextcloud.example.com.evil.com/login", False),
        ("https://evil.com/?x=https://nextcloud.example.com", False),
    ],
)
def test_allowed_urls_are_matched_by_origin_never_by_substring(tmp_path, url, expected):
    make_secret(
        tmp_path, "app", password="p",
        **{ALLOWED_URLS: "https://nextcloud.example.com"},
    )
    catalogue = Catalogue([FilesystemSource(tmp_path)])
    assert catalogue.allows("app", url) is expected


def test_a_secret_with_no_declaration_is_unrestricted(tmp_path):
    """The pragmatic default, and the listing shows which those are."""
    make_secret(tmp_path, "app", password="p")
    catalogue = Catalogue([FilesystemSource(tmp_path)])
    assert catalogue.allows("app", "https://anywhere.test/") is True
    assert catalogue.entry("app")["allowed_urls"] == []


def test_a_secret_that_does_not_exist_is_allowed_nowhere(tmp_path):
    assert Catalogue([FilesystemSource(tmp_path)]).allows("nope", "https://x.test") is False


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://Example.COM/path", "https://example.com"),
        ("https://example.com:8443/x", "https://example.com:8443"),
        ("not a url", ""),
        ("", ""),
    ],
)
def test_origin_keeps_scheme_host_and_port_and_nothing_else(url, expected):
    assert origin(url) == expected


# ---- traversal ----------------------------------------------------------------


def test_a_secret_name_cannot_escape_its_directory(source):
    from kubed.selenium_flow.flows import InvalidName

    with pytest.raises(InvalidName):
        source._dir("../../etc")
    assert source.entry("../../etc") is None
    assert source.value("../../etc", "passwd") is None


def test_a_symlinked_secret_directory_is_refused(tmp_path):
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    (outside / "token").write_text("leaked")
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    source = FilesystemSource(root)
    assert source.names() == []
    assert source.entry("linked") is None


# ---- configuration -------------------------------------------------------------


def test_secrets_are_off_unless_directories_are_named():
    assert secrets.from_env({}) is None
    assert secrets.from_env({"SECRETS_DIRS": "   "}) is None


def test_directories_are_separated_like_path():
    assert secrets.directories({"SECRETS_DIRS": "/a:/b:/c"}) == ["/a", "/b", "/c"]
    assert secrets.directories({"SECRETS_DIRS": "/a::/b"}) == ["/a", "/b"]


def test_naming_directories_turns_them_on(tmp_path):
    catalogue = secrets.from_env({"SECRETS_DIRS": str(tmp_path)})
    assert catalogue is not None
    assert len(catalogue.sources) == 1


# ---- the surface ---------------------------------------------------------------


@pytest.fixture
def secret_server(tmp_path, monkeypatch):
    from kubed.selenium_flow.server import SeleniumMCP

    from .conftest import NAMED, TOKEN

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    make_secret(
        tmp_path / "secrets-src", "nextcloud-admin", username="admin",
        password="hunter2",
        **{DESCRIPTION: "Homelab Nextcloud", ALLOWED_URLS: "https://nc.example.com"},
    )
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        secrets_dirs=str(tmp_path / "secrets-src"),
        flow_data_dir=str(tmp_path / "flows"),
    )
    monkeypatch.setattr(server.sessions, "key", lambda: NAMED)
    return server


async def test_the_catalogue_is_a_resource_with_a_tool_mirroring_it(secret_server,
                                                                    monkeypatch):
    from kubed.selenium_flow import resources as resources_module

    names = {t.name for t in await secret_server.mcp.list_tools()}
    assert secrets.LIST_TOOL not in names  # hidden from a client with resources

    monkeypatch.setattr(resources_module, "_http", lambda: ({"resources": "off"}, {}))
    names = {t.name for t in await secret_server.mcp.list_tools()}
    assert secrets.LIST_TOOL in names


async def test_listing_secrets_never_returns_a_value(secret_server):
    tool = await secret_server.mcp.get_tool(secrets.LIST_TOOL)
    result = tool.fn()
    assert result["count"] == 1
    entry = result["secrets"][0]
    assert entry["keys"] == ["password", "username"]
    assert entry["description"] == "Homelab Nextcloud"
    assert "hunter2" not in str(result)


async def test_no_tool_on_this_server_returns_a_secret_value(secret_server,
                                                             monkeypatch):
    """The hard rule, asserted across the whole surface rather than assumed.

    Every tool that can be called without a browser is called, and none of them
    may produce the value. If a future tool ever grows a way to read one, this
    is what should fail.
    """
    from kubed.selenium_flow import resources as resources_module

    monkeypatch.setattr(resources_module, "_http", lambda: ({"resources": "off"}, {}))
    # A saved flow that BINDS the secret is the case that matters most: the
    # document names it, and must never carry it.
    save = await secret_server.mcp.get_tool("save_flow")
    await save.fn(
        name="login",
        steps=[
            {
                "tool": "write",
                "params": {"css": "#password"},
                "valueFrom": {
                    "text": {"secret": {"name": "nextcloud-admin", "key": "password"}}
                },
            }
        ],
    )

    readable = {
        secrets.LIST_TOOL,
        "current_session",
        "selenium_flow_skill",
        "list_flows",
        "flow_schema",
    }
    for name in readable:
        tool = await secret_server.mcp.get_tool(name)
        result = tool.fn()
        if hasattr(result, "__await__"):
            result = await result
        assert "hunter2" not in str(result), name

    read = await secret_server.mcp.get_tool("get_flow")
    assert "hunter2" not in str(read.fn(name="login"))


async def test_the_listing_tool_says_it_only_reads(secret_server, monkeypatch):
    from kubed.selenium_flow import resources as resources_module

    monkeypatch.setattr(resources_module, "_http", lambda: ({"resources": "off"}, {}))
    tools = {t.name: t for t in await secret_server.mcp.list_tools()}
    hints = tools[secrets.LIST_TOOL].annotations
    assert hints.read_only_hint is True
    # It reads a directory this server can already see: no browser, no Grid.
    assert hints.open_world_hint is False


async def test_with_no_directories_the_tool_says_so(monkeypatch):
    from kubed.selenium_flow.server import SeleniumMCP

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    server = SeleniumMCP(grid_url="http://grid.invalid:4444")
    assert server.secrets is None
    tool = await server.mcp.get_tool(secrets.LIST_TOOL)
    with pytest.raises(ValueError, match="SECRETS_DIRS"):
        tool.fn()


def test_the_endpoint_serves_the_catalogue_and_needs_the_token(secret_server):
    from starlette.testclient import TestClient

    from .conftest import TOKEN

    client = TestClient(secret_server.mcp.http_app())
    assert client.get("/secrets").status_code == 401
    response = client.get("/secrets", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json()["secrets"][0]["name"] == "nextcloud-admin"
    assert "hunter2" not in response.text
