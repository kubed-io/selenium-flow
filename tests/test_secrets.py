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
                "params": {"css": "#password", "value_from": {"secret": {"name": "nextcloud-admin", "key": "password"}}},
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


# ---- what the review caught ---------------------------------------------------


def test_a_declaration_that_parses_to_nothing_allows_nothing(tmp_path):
    """One typo in a metadata file must not turn a leashed credential into an
    unleashed one. A broken leash is still a leash."""
    make_secret(tmp_path, "app", password="p", **{ALLOWED_URLS: "nextcloud.example.com"})
    catalogue = Catalogue([FilesystemSource(tmp_path)])
    assert catalogue.allows("app", "https://nextcloud.example.com/") is False
    assert catalogue.allows("app", "https://anywhere.test/") is False


def test_a_broken_leash_is_published_so_an_operator_can_see_it(tmp_path):
    make_secret(tmp_path, "app", password="p", **{ALLOWED_URLS: "not a url\n"})
    entry = Catalogue([FilesystemSource(tmp_path)]).entry("app")
    assert entry["restricted"] is True
    assert entry["allowed_urls"] == []
    # Not echoed: a line is rebuilt from the parts that are safe to publish, and
    # one with no origin in it has none of those.
    assert entry["allowed_urls_rejected"] == ["<a line that is not a URL>"]


def test_one_bad_line_invalidates_the_whole_declaration(tmp_path):
    """Not "use the lines that parsed": the author wrote two permissions and
    only one of them means anything, so the file has to be fixed."""
    make_secret(
        tmp_path, "app", password="p",
        **{ALLOWED_URLS: "https://good.example.com\noops\n"},
    )
    catalogue = Catalogue([FilesystemSource(tmp_path)])
    assert catalogue.allows("app", "https://good.example.com/") is False


def test_a_declaration_carrying_a_path_is_refused_not_trimmed(tmp_path):
    """Origins are what §F1.27 compares, so https://host/admin and https://host
    are the same permission. Quietly widening one into the other makes the file
    say less than its author wrote."""
    make_secret(
        tmp_path, "app", password="p",
        **{ALLOWED_URLS: "https://nextcloud.example.com/admin"},
    )
    catalogue = Catalogue([FilesystemSource(tmp_path)])
    assert catalogue.entry("app")["allowed_urls_rejected"] == [
        "https://nextcloud.example.com (+ a path)"
    ]
    assert catalogue.allows("app", "https://nextcloud.example.com/admin") is False


@pytest.mark.parametrize(
    "line", ["https://host", "https://host/", "https://host:8443", "HTTPS://Host/"]
)
def test_a_bare_origin_is_accepted_with_or_without_a_trailing_slash(tmp_path, line):
    make_secret(tmp_path, "app", password="p", **{ALLOWED_URLS: line})
    entry = Catalogue([FilesystemSource(tmp_path)]).entry("app")
    assert "allowed_urls_rejected" not in entry
    assert len(entry["allowed_urls"]) == 1


def test_a_secret_with_no_declaration_is_marked_unrestricted(tmp_path):
    make_secret(tmp_path, "app", password="p")
    entry = Catalogue([FilesystemSource(tmp_path)]).entry("app")
    assert entry["restricted"] is False
    assert Catalogue([FilesystemSource(tmp_path)]).allows("app", "https://x.test") is True


def test_an_unreadable_directory_does_not_take_down_the_catalogue(tmp_path):
    """"Unreadable is absent" is a promise this module makes, and iterdir can
    raise PermissionError."""
    make_secret(tmp_path, "readable", token="t")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "token").write_text("t")
    locked.chmod(0o000)
    try:
        catalogue = Catalogue([FilesystemSource(tmp_path)])
        names = [s["name"] for s in catalogue.listing()["secrets"]]
        assert "readable" in names
    finally:
        locked.chmod(0o755)


def test_an_unreadable_root_is_no_secrets_rather_than_a_crash(tmp_path):
    root = tmp_path / "locked-root"
    root.mkdir()
    root.chmod(0o000)
    try:
        assert FilesystemSource(root).names() == []
    finally:
        root.chmod(0o755)


# ---- binding: the one path that reads a value ---------------------------------


@pytest.fixture
def bindable(tmp_path):
    make_secret(
        tmp_path, "nextcloud", username="admin", password="hunter2",
        **{ALLOWED_URLS: "https://nc.example.com"},
    )
    make_secret(tmp_path, "anywhere", token="free")
    return Catalogue([FilesystemSource(tmp_path)])


def test_a_bind_returns_the_value_to_exactly_one_caller(bindable):
    value = secrets.bind(
        bindable, {"secret": {"name": "nextcloud", "key": "password"}},
        "https://nc.example.com/login",
    )
    assert value == "hunter2"


def test_a_bind_on_a_page_the_secret_does_not_allow_is_refused(bindable):
    with pytest.raises(secrets.Refused, match="may not be used"):
        secrets.bind(
            bindable, {"secret": {"name": "nextcloud", "key": "password"}},
            "https://evil.test/login",
        )


def test_the_origin_suffix_attack_is_refused_at_bind_time(bindable):
    with pytest.raises(secrets.Refused):
        secrets.bind(
            bindable, {"secret": {"name": "nextcloud", "key": "password"}},
            "https://nc.example.com.evil.test/login",
        )


def test_an_unrestricted_secret_binds_anywhere(bindable):
    assert secrets.bind(
        bindable, {"secret": {"name": "anywhere", "key": "token"}},
        "https://wherever.test/",
    ) == "free"


@pytest.mark.parametrize("tool", ["execute_script", "navigate", "press_key", "extract"])
def test_only_write_may_receive_a_secret(bindable, tool):
    """A script is arbitrary code; a URL lands in history, the referrer and our
    own session record. Neither may carry a credential (§F1.28)."""
    with pytest.raises(secrets.Refused, match="cannot be bound into"):
        secrets.bind(
            bindable, {"secret": {"name": "anywhere", "key": "token"}},
            "https://x.test/", tool=tool,
        )


def test_upload_file_says_not_yet_rather_than_never(bindable):
    """A credentials file is a plausible later case, and the refusal should say
    which kind of no it is."""
    with pytest.raises(secrets.Refused, match="cannot take a secret yet"):
        secrets.bind(
            bindable, {"secret": {"name": "anywhere", "key": "token"}},
            "https://x.test/", tool="upload_file",
        )


def test_a_missing_secret_or_key_says_what_there_is(bindable):
    with pytest.raises(secrets.Refused, match="list_secrets"):
        secrets.bind(bindable, {"secret": {"name": "nope", "key": "k"}},
                     "https://x.test/")
    with pytest.raises(secrets.Refused, match="has no key") as caught:
        secrets.bind(bindable, {"secret": {"name": "nextcloud", "key": "nope"}},
                     "https://nc.example.com/")
    assert "password" in str(caught.value)


def test_a_refusal_never_carries_the_value(bindable):
    for source, url in (
        ({"secret": {"name": "nextcloud", "key": "password"}}, "https://evil.test/"),
        ({"secret": {"name": "nextcloud", "key": "nope"}}, "https://nc.example.com/"),
    ):
        try:
            secrets.bind(bindable, source, url)
        except secrets.Refused as exc:
            assert "hunter2" not in str(exc)


def test_the_bindable_set_matches_what_the_validator_enforces():
    """Two modules name this, and they must not drift: flowdoc refuses at save,
    secrets refuses at bind."""
    from kubed.selenium_flow.flowdoc import BINDABLE_TOOLS

    assert BINDABLE_TOOLS == secrets.BINDABLE


async def test_a_login_flow_types_a_secret_it_never_shows(tmp_path, monkeypatch):
    """The whole arsenal, end to end: a saved flow, a bound secret, one call."""
    from kubed.selenium_flow import flowapi
    from kubed.selenium_flow.server import SeleniumMCP

    from .conftest import NAMED, TOKEN

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    make_secret(
        tmp_path / "secrets", "nextcloud", username="admin", password="hunter2",
        **{ALLOWED_URLS: "https://nc.example.com"},
    )
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        secrets_dirs=str(tmp_path / "secrets"),
        flow_data_dir=str(tmp_path / "flows"),
    )
    monkeypatch.setattr(server.sessions, "key", lambda: NAMED)
    monkeypatch.setattr(server.sessions, "resolve", lambda key, sid: "browser-1")
    typed = []
    monkeypatch.setattr(
        server.actions, "page",
        lambda sid: {"url": "https://nc.example.com/login", "title": "Log in"},
    )
    monkeypatch.setattr(
        server.actions, "write",
        lambda sid, text, **kw: typed.append(text)
        or {"value": text, "url": "https://nc.example.com/", "title": "Home"},
    )

    save = await server.mcp.get_tool("save_flow")
    await save.fn(
        name="login",
        steps=[
            {
                "tool": "write",
                "params": {"css": "#password", "value_from": {"secret": {"name": "nextcloud", "key": "password"}}},
            }
        ],
    )
    run_flow = await server.mcp.get_tool(flowapi.RUN_TOOL)
    report = run_flow.fn(name="login")

    assert report["status"] == "ok"
    # It reached the browser...
    assert typed == ["hunter2"]
    # ...and nothing anywhere in the report says so.
    assert "hunter2" not in str(report)
    assert report["steps"][0]["summary"].endswith("text=<hidden>")


def test_the_audit_trail_never_contains_a_value(bindable, caplog):
    """The audit line names the secret and the key it was looked up by, which
    is the point of an audit line. Both are read off the catalogue's own entry
    rather than the caller's reference, so the line records what was *resolved*
    — and nothing in it is derived from the `{"secret": ...}` object a scanner
    reads as the credential itself.
    """
    import logging

    with caplog.at_level(logging.INFO, logger="kubed.selenium_flow.secrets"):
        secrets.bind(
            bindable, {"secret": {"name": "nextcloud", "key": "password"}},
            "https://nc.example.com/login",
        )
    logged = "\n".join(r.getMessage() for r in caplog.records)
    # The identifiers are there — an audit line without them says nothing.
    assert "nextcloud/password" in logged
    # Compared whole rather than as a substring: "is this URL in that string"
    # is the shape of check that lets nc.example.com.evil.test through, and it
    # should not be modelled even in a test.
    assert any(part == "https://nc.example.com" for part in logged.split())
    # The credential is not.
    assert "hunter2" not in logged


def test_a_refused_bind_is_logged_loudly_and_still_without_the_value(bindable, caplog):
    import logging

    with (
        caplog.at_level(logging.INFO, logger="kubed.selenium_flow.secrets"),
        pytest.raises(secrets.Refused),
    ):
        secrets.bind(
            bindable, {"secret": {"name": "nextcloud", "key": "password"}},
            "https://evil.test/login",
        )
    refusals = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert refusals, "a credential used somewhere it may not be is a warning"
    assert "hunter2" not in "\n".join(r.getMessage() for r in caplog.records)


def test_an_unparseable_port_is_a_rejected_line_not_a_crash(tmp_path):
    """`SplitResult.port` PARSES, and raises for anything out of range — so one
    bad line would have taken down catalogue construction rather than being
    recorded as rejected."""
    make_secret(
        tmp_path, "app", password="p",
        **{ALLOWED_URLS: "https://host:99999\nhttps://good.test\n"},
    )
    catalogue = Catalogue([FilesystemSource(tmp_path)])
    entry = catalogue.entry("app")
    # The port is what could not be parsed, so there is no origin to show.
    assert entry["allowed_urls_rejected"] == ["<a line that is not a URL>"]
    assert catalogue.allows("app", "https://good.test/") is False  # one bad line voids it


def test_credentials_in_an_allowed_url_are_refused(tmp_path):
    """netloc carries userinfo, so accepting one would put a password in the
    permission file and make the same site read as two origins."""
    make_secret(
        tmp_path, "app", password="p",
        **{ALLOWED_URLS: "https://user:s3cr3t@host.test"},
    )
    entry = Catalogue([FilesystemSource(tmp_path)]).entry("app")
    # Reported so an operator can find the line — with the credential taken
    # out, since /secrets is the last place one should turn up.
    assert entry["allowed_urls_rejected"] == ["https://host.test (+ credentials)"]
    assert "s3cr3t" not in str(entry)
    assert "user" not in str(entry)


def test_a_rejected_line_never_publishes_its_query(tmp_path):
    """The sharpest shape of this: a line is refused *because* it carries a
    credential, and `?token=` is how one usually arrives. Removing userinfo and
    echoing the rest handed it straight back through /secrets."""
    make_secret(
        tmp_path, "app", password="p",
        **{ALLOWED_URLS: "https://host.test/login?token=hunter2"},
    )
    entry = Catalogue([FilesystemSource(tmp_path)]).entry("app")
    assert entry["allowed_urls_rejected"] == ["https://host.test (+ a path)"]
    assert "hunter2" not in str(entry)
    assert "token" not in str(entry)


def test_a_rejected_line_still_says_which_host_it_was(tmp_path):
    """Rebuilt, not blanked: an operator with three permission lines has to be
    able to tell which one is wrong, and the origin is the part that is safe."""
    make_secret(
        tmp_path, "app", password="p",
        **{ALLOWED_URLS: "https://good.test\nhttps://other.test:8443/admin\n"},
    )
    entry = Catalogue([FilesystemSource(tmp_path)]).entry("app")
    assert entry["allowed_urls_rejected"] == ["https://other.test:8443 (+ a path)"]


def test_an_origin_never_carries_userinfo():
    assert origin("https://user:pass@example.com/x") == "https://example.com"


# ---- the HTTP binding path, which had no test of its own --------------------


@pytest.fixture
def bound_http(tmp_path, monkeypatch):
    """A real server with a real secret, and doubles only at the browser."""
    from starlette.testclient import TestClient

    from kubed.selenium_flow.server import SeleniumMCP

    from .conftest import TOKEN

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    make_secret(
        tmp_path, "nextcloud", password="hunter2",
        **{ALLOWED_URLS: "https://nc.example.com"},
    )
    typed = []

    def write(
        self, session_id, text, xpath=None, url=None, clear=True, submit=False,
        wait_timeout=30, css=None, read_back=True,
    ):
        # The signature matters: `routes.py` derives the accepted request fields
        # from it, so a double taking **kwargs would silently drop `url` and
        # make the navigation refusal untestable.
        typed.append((text, read_back))
        return {
            # As the real one does: the read does not happen when it is off.
            "value": text if read_back else None,
            "url": "https://nc.example.com/",
            "title": "Home",
        }

    # Patched on the CLASS, before the server is built: `routes.py` binds each
    # method at registration time, so patching the instance afterwards is too
    # late and the real one dials the Grid.
    from kubed.selenium_flow.actions import Actions

    monkeypatch.setattr(Actions, "write", write)
    monkeypatch.setattr(
        Actions, "page",
        lambda self, sid: {"url": "https://nc.example.com/login", "title": "Log in"},
    )
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        secrets_dirs=str(tmp_path),
    )
    return TestClient(server.mcp.http_app()), typed


AUTH = {"Authorization": "Bearer test-token-abc123"}


def test_an_http_caller_can_bind_a_secret_it_never_sees(bound_http):
    """The HTTP half of the capability. It existed with no test of its own —
    the end-to-end one drives the MCP tool, and test_surfaces only compares
    registration sets, so resolution, read_back and scrubbing were all free to
    regress silently."""
    client, typed = bound_http
    response = client.post(
        "/browser/write",
        json={
            "session_id": "b1",
            "css": "#password",
            "value_from": {"secret": {"name": "nextcloud", "key": "password"}},
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    # It reached the browser...
    assert typed == [("hunter2", False)]
    # ...the read-back was turned off, not merely redacted...
    # ...and nothing came back.
    assert "hunter2" not in response.text
    assert response.json()["value"] is None
    assert response.json()["value_from"] == "secret"


def test_an_http_bind_on_a_disallowed_page_is_refused(bound_http, monkeypatch):
    client, typed = bound_http
    response = client.post(
        "/browser/write",
        json={
            "session_id": "b1",
            "css": "#password",
            "value_from": {"secret": {"name": "nextcloud", "key": "nope"}},
        },
        headers=AUTH,
    )
    assert response.status_code == 400
    assert typed == []
    assert "hunter2" not in response.text


def test_an_http_bind_may_not_also_navigate(bound_http):
    """`_at` navigates before typing, so the leash would be checked against the
    page being left."""
    client, typed = bound_http
    response = client.post(
        "/browser/write",
        json={
            "session_id": "b1",
            "css": "#password",
            "url": "https://evil.test/",
            "value_from": {"secret": {"name": "nextcloud", "key": "password"}},
        },
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "may not also navigate" in response.json()["error"]
    assert typed == []


@pytest.mark.parametrize(
    "value_from", ["secret", {"secret": "x"}, {}, {"param": "email"}, 7]
)
def test_a_malformed_binding_over_http_is_a_400_not_a_500(bound_http, value_from):
    """The HTTP surface hands raw JSON to the shared binder, without the typed
    model the MCP parameter has — so the binder has to check the shape itself
    or a caller's mistake reads as our outage."""
    client, _ = bound_http
    response = client.post(
        "/browser/write",
        json={"session_id": "b1", "css": "#p", "value_from": value_from},
        headers=AUTH,
    )
    assert response.status_code == 400, response.text


def test_an_http_caller_cannot_ask_for_the_read_back_to_be_skipped(bound_http):
    """`read_back` is an internal switch, not a request field: accepting it
    would let a caller get `value: null` with no binding at all."""
    client, typed = bound_http
    response = client.post(
        "/browser/write",
        json={"session_id": "b1", "css": "#p", "text": "plain", "read_back": False},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    # The caller asked for False and the action was called with its default,
    # so the field was dropped rather than honoured.
    assert typed == [("plain", True)]
    assert response.json()["value"] == "plain"


def test_port_zero_is_a_port_and_not_the_default(tmp_path):
    """This is the exact-origin boundary, so an edge that collapses two origins
    into one is the kind that matters."""
    assert origin("https://host:0/") == "https://host:0"
    make_secret(tmp_path, "app", password="p", **{ALLOWED_URLS: "https://host:0"})
    catalogue = Catalogue([FilesystemSource(tmp_path)])
    assert catalogue.allows("app", "https://host:0/x") is True
    assert catalogue.allows("app", "https://host/x") is False


async def test_a_direct_bound_write_never_stores_the_page_it_typed_on(
    tmp_path, monkeypatch
):
    """The third surface of the same rule. It decided by comparing the URL with
    its scrubbed form, so a secret whose value is the marker compared equal and
    the credential URL went into the session record — from where a reattach
    would have navigated back to it.
    """
    from kubed.selenium_flow import flowrun
    from kubed.selenium_flow.server import SeleniumMCP

    from .conftest import NAMED, TOKEN

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    make_secret(
        tmp_path, "nextcloud", password=flowrun.HIDDEN,
        **{ALLOWED_URLS: "https://nc.example.com"},
    )
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        secrets_dirs=str(tmp_path),
    )
    monkeypatch.setattr(server.sessions, "key", lambda: NAMED)
    monkeypatch.setattr(server.sessions, "resolve", lambda key, sid: "browser-1")
    monkeypatch.setattr(
        server.actions, "page",
        lambda sid: {"url": "https://nc.example.com/login", "title": "Log in"},
    )
    monkeypatch.setattr(
        server.actions, "write",
        lambda sid, text, **kw: {
            "value": None,
            # Submitted, so the page carries what was typed.
            "url": f"https://nc.example.com/?q={text}",
            "title": "Home",
        },
    )
    touched = []
    monkeypatch.setattr(
        server.sessions, "touch",
        lambda key, url, sid: touched.append(url),
    )

    write = await server.mcp.get_tool("write")
    result = write.fn(
        css="#password",
        value_from={"secret": {"name": "nextcloud", "key": "password"}},
    )
    # The page the value reached is never remembered, whatever the value is —
    # but the session is still touched, because withholding the page must not
    # also stop the clock that keeps the session alive.
    assert touched == [None]
    assert result["url"] == f"https://nc.example.com/?q={flowrun.HIDDEN}"


async def test_a_direct_bound_write_still_remembers_an_untouched_page(
    tmp_path, monkeypatch
):
    """The other half: refusing to remember every bound write would lose the
    session's page for the ordinary case, where the value never reaches the URL.
    """
    from kubed.selenium_flow.server import SeleniumMCP

    from .conftest import NAMED, TOKEN

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    make_secret(
        tmp_path, "nextcloud", password="hunter2",
        **{ALLOWED_URLS: "https://nc.example.com"},
    )
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        secrets_dirs=str(tmp_path),
    )
    monkeypatch.setattr(server.sessions, "key", lambda: NAMED)
    monkeypatch.setattr(server.sessions, "resolve", lambda key, sid: "browser-1")
    monkeypatch.setattr(
        server.actions, "page",
        lambda sid: {"url": "https://nc.example.com/login", "title": "Log in"},
    )
    monkeypatch.setattr(
        server.actions, "write",
        lambda sid, text, **kw: {
            "value": None, "url": "https://nc.example.com/home", "title": "Home",
        },
    )
    touched = []
    monkeypatch.setattr(
        server.sessions, "touch", lambda key, url, sid: touched.append(url)
    )

    write = await server.mcp.get_tool("write")
    write.fn(
        css="#password",
        value_from={"secret": {"name": "nextcloud", "key": "password"}},
    )
    assert touched == ["https://nc.example.com/home"]


def test_an_http_binding_naming_two_sources_is_refused(bound_http, monkeypatch):
    """`bind` reads `secret` and ignores whatever else is there, so the shape
    check has to happen before it. A body saying two things is malformed, not a
    request to pick one."""
    client, typed = bound_http
    response = client.post(
        "/browser/write",
        headers=AUTH,
        json={
            "session_id": "browser-1",
            "css": "#password",
            "value_from": {
                "secret": {"name": "nextcloud", "key": "password"},
                "config": {"name": "other", "key": "thing"},
            },
        },
    )
    assert response.status_code == 400
    assert "exactly one source" in response.json()["error"]
    # Refused before anything was typed.
    assert typed == []


def test_a_session_in_use_is_kept_alive_even_when_its_page_is_withheld():
    """`touch` slides the TTL as well as recording the page, and the two are
    separate facts. A login flow binding a secret every few minutes — the exact
    thing secrets exist for — expired out of the store *because* its URL was
    correctly kept out of it.

    Driven through `touch` rather than the store: `SessionRecord.at` has always
    kept the old page when given nothing, and it was `touch`'s own early return
    that threw the refresh away. A test on the store would have passed
    throughout.
    """
    from kubed.selenium_flow.store import MemoryStore, SessionRecord

    from .conftest import NAMED, manager

    clock = [1000.0]
    store = MemoryStore(ttl=60, clock=lambda: clock[0])
    store.set(
        NAMED.value, SessionRecord(session_id="browser-1", url="https://nc.test/home")
    )
    sessions = manager(store=store)

    clock[0] += 50
    # The page is withheld, the way a bound write withholds it.
    sessions.touch(NAMED, None, "browser-1")

    clock[0] += 50  # past the original expiry, inside the slid one
    kept = store.get(NAMED.value)
    assert kept is not None, "the session expired while it was being used"
    # And the page it already knew survives being touched with nothing.
    assert kept.url == "https://nc.test/home"
