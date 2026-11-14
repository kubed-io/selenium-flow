"""The two origin rules agree where it matters and differ where they must."""

from types import SimpleNamespace

import pytest

from kubed.selenium_flow import urls
from kubed.selenium_flow.core import browser
from kubed.selenium_flow.secrets import origin as exact_origin

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "https://Example.COM/path?q#f",
        "http://localhost:3000/a",
        "https://user:pass@example.com/x",
        "https://user@example.com:8443/",
        "https://host:0/",
        "about:blank",
        "",
    ],
)
def test_the_two_origin_rules_agree_on_userinfo_case_and_explicit_ports(url):
    assert exact_origin(url) == urls.origin_of(url)


@pytest.mark.parametrize(
    "url,permission,page",
    [
        ("https://host:443/", "https://host:443", "https://host"),
        ("http://host:80/", "http://host:80", "http://host"),
        ("http://[::1]:3000/", "http://::1:3000", "http://[::1]:3000"),
    ],
)
def test_they_differ_on_a_default_port_and_on_an_ipv6_literal(url, permission, page):
    """Why `secrets.origin` was not folded into `urls.origin_of`.

    The permission rule keeps a default port written out, so `https://host:443`
    is a different origin from `https://host` (test_secrets pins it); the page
    rule spells an origin as `location.origin` does. Unifying would change what a
    secret may be bound to.
    """
    assert exact_origin(url) == permission
    assert urls.origin_of(url) == page


# ---- what a caller may open (Ruling 4) ---------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://localhost:3000/a?q#f",
        "HTTPS://Example.COM",
        "  https://example.com  ",
        "about:blank",
        "ABOUT:BLANK",
    ],
)
def test_the_web_and_a_blank_page_can_be_opened(url):
    assert urls.allowed_navigation(url) == url


@pytest.mark.parametrize(
    "url,scheme",
    [
        ("file:///etc/passwd", "file"),
        ("chrome://settings", "chrome"),
        ("view-source:https://example.com", "view-source"),
        ("javascript:alert(1)", "javascript"),
        ("data:text/html,<b>hi</b>", "data"),
        ("about:config", "about"),
        ("about:blank#x", "about"),
        ("blob:https://example.com/1", "blob"),
        ("FILE:///etc/passwd", "file"),
        (" \tjava\nscript:alert(1)", "javascript"),
    ],
)
def test_every_other_scheme_is_refused_by_name(url, scheme):
    with pytest.raises(ValueError) as refused:
        urls.allowed_navigation(url)
    assert str(refused.value) == (
        f"only http(s) URLs can be opened here; {scheme}: cannot"
    )


def test_a_url_with_no_scheme_is_refused_without_quoting_it():
    with pytest.raises(ValueError) as refused:
        urls.allowed_navigation("example.com/path")
    assert str(refused.value) == (
        "only http(s) URLs can be opened here; a URL with no scheme cannot"
    )


@pytest.mark.parametrize("url, route", [
    ("https://p.test/", None),
    ("https://p.test/#", None),
    ("https://p.test/#top", None),
    ("https://p.test/cb#access_token=x&state=1", None),
    ("https://p.test/#!", None),
    ("https://p.test/#!top", None),
    ("https://p.test/#/", ""),
    ("https://p.test/#!/", ""),
    ("https://p.test/#/?x=1", ""),
    ("https://p.test/#/auth/login", "/auth/login"),
    ("https://p.test/#!/auth/login/", "/auth/login"),
    ("https://p.test/#/auth/login?next=/a", "/auth/login"),
    ("http://[::1/#/a", None),
    (None, None),
])
def test_a_route_is_a_fragment_shaped_like_a_path(url, route):  # rulings 1, 6
    assert urls.route_of(url) == route


@pytest.mark.parametrize("url, query", [
    ("https://p.test/#/workspace?file-id=1", "file-id=1"),
    ("https://p.test/#!/a/?x=1", "x=1"),
    ("https://p.test/#/a", ""),
    ("https://p.test/?x=1#/a", ""),
    ("https://p.test/#top?x=1", ""),
    ("http://[::1/#/a?x=1", ""),
])
def test_a_routes_query_is_read_only_from_a_route(url, query):  # ruling 11
    assert urls.route_query(url) == query


def test_the_page_key_carries_the_route():  # ruling 2
    assert urls.page_key("https://P.test/#/workspace?file-id=1") == ("p.test", 443, "", "/workspace")
    assert urls.page_key("https://p.test/docs/#top") == ("p.test", 443, "/docs", None)


@pytest.mark.parametrize("url, kept", [
    ("https://u:p@p.test/#/workspace?team-id=T", "https://p.test/#/workspace"),
    ("https://p.test/app/#!/settings/", "https://p.test/app/#!/settings/"),
    ("https://a.test/cb#access_token=x", "https://a.test/cb"),
    ("https://a.test/docs?q=1#top", "https://a.test/docs"),
    ("https://a.test/cb#/access_token=T&token_type=bearer", "https://a.test/cb"),
    ("https://a.test/#!/access_token=T", "https://a.test/"),
])
def test_page_of_keeps_a_route_as_written_without_its_query(url, kept):  # rulings 9, 13
    assert urls.page_of(url) == kept


@pytest.mark.parametrize("a, b, same", [
    ("https://p.test/#/auth/login", "https://p.test/#/dashboard", False),
    ("https://p.test/#/auth/login", "https://p.test/#!/auth/login/", True),
    ("https://p.test/docs#section", "https://p.test/docs#other", True),
    ("https://p.test/docs", "https://p.test/docs/#top", True),
    ("https://p.test/#/dashboard", "https://p.test/", False),                    # ruling 12, Review Focus 4
    ("https://p.test/#/", "https://p.test/", True),                              # ruling 12, the root route
    ("https://p.test/#/?x=1", "https://p.test/", False),
    ("https://p.test/#/workspace?file-id=A", "https://p.test/#/workspace?file-id=B", False),  # ruling 11
    ("https://p.test/a?p=1", "https://p.test/a", False),
    ("https://P.test:443/a", "https://p.test/a", True),
    ("https://u:p@p.test/a", "https://p.test/a", False),                      # ruling 12, userinfo (Dr K)
    ("https://u:p@p.test/a", "https://u:p@p.test/a/", True),
    ("about:blank", "about:blank", True),                                        # Review Focus 5
    ("about:blank", "https://p.test/", False),
])
def test_one_page_for_url(a, b, same):
    assert urls.same_page(a, b) is same
    assert urls.same_page(b, a) is same


def test_ensure_url_moves_between_routes_and_not_between_anchors(monkeypatch):  # Review Focus 1
    went = []
    monkeypatch.setattr(browser, "go", lambda driver, url: went.append(url))
    driver = SimpleNamespace(current_url="https://p.test/#/auth/login")
    assert browser.ensure_url(driver, "https://p.test/#/dashboard/recent") is True
    driver.current_url = "https://p.test/#/workspace?file-id=A"
    assert browser.ensure_url(driver, "https://p.test/#/workspace?file-id=B") is True
    driver.current_url = "https://p.test/docs#section"
    assert browser.ensure_url(driver, "https://p.test/docs#other") is False
    assert went == ["https://p.test/#/dashboard/recent", "https://p.test/#/workspace?file-id=B"]
