"""Where a flow applies: its sites and paths, and the matcher that reads them.

A site without a port matches any port, which finds a flow and would be wrong
for a password's leash, so the matcher shares no code with `secrets` (ruling 8).
"""

import ast
from pathlib import Path

import pytest

from kubed.selenium_flow import secrets, urls
from kubed.selenium_flow.flows.places import Places, problems

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).parent.parent / "kubed" / "selenium_flow"

SITE = {"sites": ["example.com"]}
PORTED = {"sites": ["localhost:8080"]}


@pytest.mark.parametrize(
    "document, url, applies",
    [
        (SITE, "https://example.com/x", True),
        (SITE, "http://example.com:8443/x", True),
        (PORTED, "http://localhost:8080/login", True),
        (PORTED, "http://localhost:3000/login", False),
        (PORTED, "http://localhost/login", False),
        ({"sites": ["example.com:443"]}, "https://example.com/", True),
        ({"sites": ["Example.COM"]}, "https://example.com/", True),
        ({"sites": ["*.example.com"]}, "https://a.example.com/", True),
        ({"sites": ["*.example.com"]}, "https://a.b.example.com/", True),
        ({"sites": ["*.example.com"]}, "https://example.com/", False),
        ({"sites": ["[::1]:8080"]}, "http://[::1]:8080/", True),
        ({"sites": ["bücher.example"]}, "https://xn--bcher-kva.example/", True),
        ({"sites": ["example.com"]}, "https://user:pw@example.com/", True),
        ({"paths": ["/login"]}, "https://any.test/login", True),
        ({"paths": ["/login"]}, "https://any.test/login/", True),
        ({"paths": ["/login/"]}, "https://any.test/login", True),
        ({"paths": ["/"]}, "https://any.test", True),
        ({"paths": ["/login"]}, "https://any.test/login?next=/a#top", True),
        ({"paths": ["/login"]}, "https://any.test/Login", False),
        ({"paths": ["/apps/*/settings"]}, "https://t/apps/42/settings", True),
        ({"paths": ["/apps/*/settings"]}, "https://t/apps/4/2/settings", False),
        ({"paths": ["/report-*"]}, "https://t/report-2026", True),
        ({"paths": ["/docs/**"]}, "https://t/docs", True),
        ({"paths": ["/docs/**"]}, "https://t/docs/a/b", True),
        ({"paths": ["/docs/**"]}, "https://t/doc", False),
        ({"paths": ["/a[1]"]}, "https://t/a[1]", True),
        (
            {"sites": ["example.com"], "paths": ["/login"]},
            "https://example.com/admin",
            False,
        ),
        ({"paths": ["/**"]}, "about:blank", False),
        ({"paths": ["/**"]}, "data:text/html,x", False),
        ({"paths": ["/**"]}, None, False),
    ],
)
def test_where_a_flow_applies(document, url, applies):
    assert Places.parse(document).applies(url) is applies


def test_a_flow_with_neither_declares_nothing():
    assert Places.parse({"name": "x", "steps": []}) is None
    assert Places.parse({"sites": None, "paths": None}) is None


@pytest.mark.parametrize(
    "url", ["http://example.com:99999/", "http://[::1/", "", "not a url", None]
)
def test_page_key_never_raises(url):
    assert urls.page_key(url) is None


def test_page_key_reads_host_port_and_path():
    assert urls.page_key("http://Localhost:8080/login/?q=1") == (
        "localhost",
        8080,
        "/login",
        None,
    )
    assert urls.page_key("https://example.com") == ("example.com", 443, "", None)


@pytest.mark.parametrize(
    "document, sentence",
    [
        (
            {"sites": "example.com"},
            "sites must be a list of sites, such as [localhost:8080, example.com]",
        ),
        (
            {"sites": [1]},
            "sites must be a list of sites, such as [localhost:8080, example.com]",
        ),
        ({"sites": []}, "sites is empty: leave it out to match any site"),
        (
            {"sites": ["https://example.com/login"]},
            "sites: 'https://example.com/login' has a scheme; write example.com: a site matches http and https alike",
        ),
        (
            {"sites": ["me@example.com:81"]},
            "sites: 'me@example.com:81' has a user name; write example.com:81",
        ),
        (
            {"sites": ["example.com/login"]},
            "sites: 'example.com/login' has a path; write example.com, and put the path in paths",
        ),
        (
            {"sites": ["example.com:80?x=1"]},
            "sites: 'example.com:80?x=1' has a path; write example.com:80, and put the path in paths",
        ),
        (
            {"sites": ["*"]},
            "sites: '*' matches every site; leave sites out to match any site",
        ),
        (
            {"sites": ["a.*.com"]},
            "sites: 'a.*.com' has a * that is not at the front; write *. in front only, as in *.example.com",
        ),
        (
            {"sites": ["*.*.com"]},
            "sites: '*.*.com' has a * that is not at the front; write *. in front only, as in *.example.com",
        ),
        (
            {"sites": ["example.com:0"]},
            "sites: 'example.com:0' has port 0; a port is a number from 1 to 65535",
        ),
        (
            {"sites": ["example.com:70000"]},
            "sites: 'example.com:70000' has port 70000; a port is a number from 1 to 65535",
        ),
        ({"sites": [""]}, "sites: '' is not a host name"),
        ({"sites": [":80"]}, "sites: ':80' is not a host name"),
        ({"sites": ["a..b"]}, "sites: 'a..b' is not a host name"),
        ({"sites": ["[example]"]}, "sites: '[example]' is not a host name"),
        ({"sites": ["[::1"]}, "sites: '[::1' is not a host name"),
        ({"sites": ["[::1:8080"]}, "sites: '[::1:8080' is not a host name"),
        (
            {"paths": "/login"},
            "paths must be a list of paths, such as [/login, /admin/login]",
        ),
        ({"paths": []}, "paths is empty: leave it out to match any path"),
        ({"paths": ["login"]}, "paths: 'login' must start with /"),
        (
            {"paths": ["#/auth/*"]},
            "paths: '#/auth/*' must start with /; write /#/auth/* for a route on the root path",
        ),
        (
            {"paths": ["/#/a#b"]},
            "paths: '/#/a#b' has more than one #; write one, before the route, as in /#/auth/*",
        ),
        (
            {"paths": ["/a#top"]},
            "paths: '/a#top' has a fragment that is not a route; write /a: only a fragment starting with / or !/ is matched",
        ),
        (
            {"paths": ["/#"]},
            "paths: '/#' has a fragment that is not a route; write /: only a fragment starting with / or !/ is matched",
        ),
        (
            {"paths": ["/login?next=/a#top"]},
            "paths: '/login?next=/a#top' has a fragment that is not a route; write /login: only a fragment starting with / or !/ is matched",
        ),
        (
            {"paths": ["/login?next=/a"]},
            "paths: '/login?next=/a' has a query; write /login: a path matches whatever query follows it",
        ),
        (
            {"paths": ["/#/workspace?team-id=1"]},
            "paths: '/#/workspace?team-id=1' has a query; write /#/workspace: a path matches whatever query follows it",
        ),
        (
            {"paths": ["/a?x#/b"]},
            "paths: '/a?x#/b' has a query; write /a#/b: a path matches whatever query follows it",
        ),
        (
            {"paths": ["/#/a**"]},
            "paths: '/#/a**' has ** inside a segment; ** stands alone between slashes, as in /docs/**",
        ),
        ({"paths": ["/#!//a"]}, "paths: '/#!//a' has an empty segment (//)"),
        (
            {"paths": ["/docs/a**"]},
            "paths: '/docs/a**' has ** inside a segment; ** stands alone between slashes, as in /docs/**",
        ),
        ({"paths": ["/a//b"]}, "paths: '/a//b' has an empty segment (//)"),
    ],
)
def test_problems_say_what_to_write(document, sentence):
    assert problems(document) == [sentence]


PENPOT = {"sites": ["penpot.example"], "paths": ["/#/auth/*"]}


@pytest.mark.parametrize(
    "document, url, applies",
    [
        (PENPOT, "https://penpot.example/#/auth/login", True),
        (PENPOT, "https://penpot.example/#/dashboard/recent", False),
        (PENPOT, "https://penpot.example/#/auth/login?next=/x", True),
        (PENPOT, "https://penpot.example/", False),
        (PENPOT, "https://penpot.example/#auth", False),
        ({"paths": ["/#!/auth/*"]}, "https://t/#/auth/login", True),
        ({"paths": ["/#/auth/*"]}, "https://t/#!/auth/login", True),
        ({"paths": ["/#/"]}, "https://t/#/", True),
        ({"paths": ["/#/"]}, "https://t/", False),
        ({"paths": ["/#/"]}, "https://t/#/dashboard", False),
        ({"paths": ["/app/#/settings/**"]}, "https://t/app/#/settings", True),
        ({"paths": ["/app/#/settings/**"]}, "https://t/app/#/settings/a/b", True),
        ({"paths": ["/app/#/settings/**"]}, "https://t/#/settings", False),
        ({"paths": ["/"]}, "https://t/#/workspace?file-id=1", True),
        ({"paths": ["/"]}, "https://t/#/auth/login", True),
        ({"paths": ["/#/**"]}, "https://t/#access_token=x", False),
        ({"paths": ["/#/**"]}, "https://t/", False),
    ],
)
def test_where_a_flow_applies_by_route(document, url, applies):
    assert Places.parse(document).applies(url) is applies


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"sites": None, "paths": None},
        {"sites": [" Example.com "], "paths": [" /a/** "]},
        {"paths": ["/#/auth/*", "/app/#/settings/**", "/#!/a", "/#/"]},
    ],
)
def test_good_documents_have_no_problems(document):
    assert problems(document) == []


def test_problems_are_every_one_in_order():
    found = problems({"paths": ["x"], "sites": ["a/b", "*"]})
    assert found == [
        "sites: 'a/b' has a path; write a, and put the path in paths",
        "sites: '*' matches every site; leave sites out to match any site",
        "paths: 'x' must start with /",
    ]


def test_a_duplicate_is_harmless():
    assert problems({"sites": ["a.test", "a.test"]}) == []


def test_parse_refuses_what_problems_finds():
    with pytest.raises(ValueError):
        Places.parse({"sites": []})


def test_a_flow_site_is_not_a_secret_origin():
    assert Places.parse(SITE).applies("https://example.com:8443/x")
    assert secrets.origin("https://example.com:8443/x") != secrets.origin(
        "https://example.com/x"
    )


def test_the_matcher_and_the_leash_share_no_code():
    for path, other in (("flows/places.py", "secrets"), ("secrets.py", "places")):
        source = (PACKAGE / path).read_text()
        tree = ast.parse(source)
        imported = {
            n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        }
        imported |= {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
        }
        assert not any(other in name.split(".") for name in imported), path


@pytest.mark.parametrize(
    "pattern, url, applies",
    [
        ("/*a*a*a*a*a*a*a*b", "https://t/" + "a" * 60, False),
        ("/*a*a*a*a*a*a*a*b", "https://t/" + "a" * 60 + "b", True),
        ("/**" * 10 + "/b", "https://t" + "/a" * 25, False),
        ("/**" * 10 + "/b", "https://t" + "/a" * 25 + "/b", True),
        ("/**/*a*a*a*b/**", "https://t" + ("/" + "a" * 30) * 20, False),
        ("/#/*a*a*a*a*a*a*a*b", "https://t/#/" + "a" * 60, False),
    ],
)
def test_a_pattern_cannot_stall_the_hint(pattern, url, applies):
    """A shared flow's paths are matched on every workspace's page change, so a
    pattern built to backtrack must still answer at once."""
    import time

    places = Places.parse({"paths": [pattern]})
    began = time.perf_counter()
    assert places.applies(url) is applies
    assert time.perf_counter() - began < 0.5


def test_consecutive_double_stars_are_one():
    assert Places.parse({"paths": ["/a/**/**/**/b"]}) == Places.parse(
        {"paths": ["/a/**/b"]}
    )
