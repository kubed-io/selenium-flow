"""The snapshot by host: the Site data tab, session://site-data, and Forget.
Pure, over round 2's snapshot shape."""

import pytest

from kubed.selenium_flow import urls
from kubed.selenium_flow.core import site_data as sd

from .site_data_fakes import NOW, cookie, snapshot

pytestmark = pytest.mark.unit

APP = "https://app.example.com"


def visit(url, at=NOW):
    return {"origin": urls.origin_of(url), "url": url, "at": at}


def test_one_row_per_host_with_counts_and_both_storages_in_full():
    data = snapshot(
        cookies=[cookie("sid", "app.example.com", value="secret", http_only=True),
                 cookie("ab", ".example.com", value="b1"), cookie("kc", "sso.example.com")],
        origins={APP: {"theme": "dark", "tour": "1"}},
        session={"origin": APP, "items": {"token": "t"}},
    )
    listing = sd.view(data)
    assert listing["saved_at"] == NOW and listing["uri"] == "session://site-data"
    rows = {r["site"]: r for r in listing["sites"]}
    assert rows["app.example.com"] == {
        "site": "app.example.com", "uri": "session://site-data/app.example.com", "cookies": 2,
        "storage": [{"origin": APP, "local_storage": 2, "session_storage": 1}],
    }
    assert rows["sso.example.com"]["storage"] == [], "cookies only"
    one = sd.site_view(data, "app.example.com")
    by_name = {c["name"]: c for c in one["cookies"]}
    assert by_name["sid"]["value"] == sd.MASK, "httpOnly is masked"
    assert by_name["ab"]["value"] == "b1" and by_name["ab"]["shared"] is True
    assert one["storage"] == [
        {"origin": APP, "local_storage": {"theme": "dark", "tour": "1"},
         "session_storage": {"token": "t"}},
    ]
    assert sd.site_view(data, "nope.test") is None


def test_hosts_in_the_history_come_first_then_the_rest_alphabetically():
    data = snapshot(cookies=[cookie("c", "cdn.test"), cookie("a", "ads.test"),
                             cookie("k", "app.test"), cookie("g", "grafana.test")])
    history = [visit("https://grafana.test/d"), visit("https://app.test/x"),
               visit("https://never-saved.test/")]
    assert [r["site"] for r in sd.view(data, history)["sites"]] == [
        "grafana.test", "app.test", "ads.test", "cdn.test",
    ]
    assert [r["site"] for r in sd.view(data)["sites"]] == [
        "ads.test", "app.test", "cdn.test", "grafana.test",
    ]


def test_history_hosts_are_unique_and_newest_first():
    history = [visit("https://a.test/1"), visit("http://a.test:8080/"), visit("https://b.test/")]
    assert sd.history_hosts(history) == ["a.test", "b.test"]
    assert sd.history_hosts([]) == []


def test_empty_session_storage_is_no_row():
    data = snapshot(session={"origin": "https://app.test", "items": {}})
    assert sd.view(data)["sites"] == []
    assert sd.summary(data) is None


def test_session_storage_alone_makes_a_row():
    data = snapshot(session={"origin": "https://app.test", "items": {"t": "1"}})
    assert sd.view(data)["sites"][0]["storage"] == [
        {"origin": "https://app.test", "local_storage": 0, "session_storage": 1},
    ]
    assert sd.summary(data) == {"sites": 1, "uri": "session://site-data"}


def test_one_host_on_two_ports_keeps_each_origins_storage_apart():
    data = snapshot(
        origins={"http://localhost:3000": {"k": "a"}, "http://localhost:8080": {"k": "b"}},
        session={"origin": "http://localhost:8080", "items": {"s": "1"}},
    )
    assert sd.view(data)["sites"][0]["storage"] == [
        {"origin": "http://localhost:3000", "local_storage": 1, "session_storage": 0},
        {"origin": "http://localhost:8080", "local_storage": 1, "session_storage": 1},
    ]
    assert sd.site_view(data, "localhost")["storage"] == [
        {"origin": "http://localhost:3000", "local_storage": {"k": "a"}, "session_storage": {}},
        {"origin": "http://localhost:8080", "local_storage": {"k": "b"}, "session_storage": {"s": "1"}},
    ]


def test_a_parent_cookie_with_no_host_gets_its_own_row():
    data = snapshot(cookies=[cookie("ab", ".example.com")])
    assert [r["site"] for r in sd.view(data)["sites"]] == ["example.com"]


def test_rows_do_not_depend_on_the_order_the_cookies_came_in():
    a = [cookie("ab", ".example.com"), cookie("sid", "app.example.com")]
    first = sd.view(snapshot(cookies=a))
    assert [r["site"] for r in first["sites"]] == ["app.example.com", "example.com"]
    assert first == sd.view(snapshot(cookies=list(reversed(a))))


def test_a_sites_own_dotted_cookie_is_not_shared_but_a_parents_is():
    data = snapshot(cookies=[cookie("own", ".app.example.com"), cookie("parent", ".example.com"),
                             cookie("plain", "app.example.com")], origins={APP: {"a": "1"}})
    shown = {c["name"]: c["shared"] for c in sd.site_view(data, "app.example.com")["cookies"]}
    assert shown == {"own": False, "parent": True, "plain": False}


def test_the_site_view_names_goes_and_stays_by_the_forget_rule():
    data = snapshot(cookies=[cookie("own", "app.example.com"), cookie("dot", ".app.example.com"),
                             cookie("ab", ".example.com")])
    one = sd.site_view(data, "app.example.com")
    assert one["own_cookies"] == ["own", "dot"]
    assert one["kept_shared"] == [{"name": "ab", "domain": ".example.com", "path": "/"}]
    parent = sd.site_view(data, "example.com")
    assert parent["own_cookies"] == ["ab"] and parent["kept_shared"] == []


def test_two_shared_cookies_of_one_name_stay_apart():
    data = snapshot(cookies=[cookie("sid", ".example.com"), cookie("sid", ".example.org"),
                             cookie("sid", "app.example.com")])
    assert sd.site_view(data, "app.example.com")["kept_shared"] == [
        {"name": "sid", "domain": ".example.com", "path": "/"}
    ]
    both = snapshot(cookies=[cookie("sid", ".example.com"), cookie("sid", ".example.org")],
                    origins={"https://a.example.com": {"k": "1"}, "https://a.example.org": {"k": "1"}})
    assert sd.site_view(both, "a.example.com")["kept_shared"][0]["domain"] == ".example.com"
    assert sd.site_view(both, "a.example.org")["kept_shared"][0]["domain"] == ".example.org"


def test_forget_takes_the_hosts_storage_and_own_cookies_and_keeps_the_rest():
    data = snapshot(
        cookies=[cookie("sid", "app.example.com"), cookie("dot", ".app.example.com"),
                 cookie("ab", ".example.com"), cookie("kc", "sso.example.com")],
        origins={APP: {"a": "1"}, "https://sso.example.com": {"b": "2"}},
        session={"origin": APP, "items": {"t": "1"}},
    )
    left, removed = sd.forget(data, "app.example.com")
    assert [c["name"] for c in left["cookies"]] == ["ab", "kc"]
    assert list(left["origins"]) == ["https://sso.example.com"]
    assert left["session"] == {}
    assert left["saved_at"] == NOW, "forgetting is not a save"
    assert removed == {
        "site": "app.example.com", "cookies": ["sid", "dot"], "origins": [APP],
        "kept_shared": [{"name": "ab", "domain": ".example.com", "path": "/"}],
    }


def test_forget_of_the_session_storage_host_alone_names_its_origin():
    left, removed = sd.forget(snapshot(session={"origin": "https://app.test", "items": {"t": "1"}}),
                              "app.test")
    assert left["session"] == {} and removed["origins"] == ["https://app.test"]


def test_forget_leaves_session_storage_on_another_host():
    data = snapshot(cookies=[cookie("sid", "app.test")], session={"origin": "https://sso.test", "items": {"t": "1"}})
    left, _ = sd.forget(data, "app.test")
    assert left["session"] == {"origin": "https://sso.test", "items": {"t": "1"}}


def test_forgetting_a_parent_only_row_removes_its_dotted_cookie():
    data = snapshot(cookies=[cookie("shared", ".example.com")])
    left, removed = sd.forget(data, "example.com")
    assert removed["cookies"] == ["shared"] and removed["kept_shared"] == []
    assert sd.view(left)["sites"] == []


def test_summary_is_none_when_empty():
    assert sd.summary({}) is None
    assert sd.summary(snapshot(cookies=[cookie("sid", "app.test")])) == {
        "sites": 1, "uri": "session://site-data",
    }


def test_a_jar_of_many_domains_is_not_rescanned_per_host(monkeypatch):
    """Every host used to rescan the whole jar, and every detail rebuilt the
    listing: cookies x hosts x hosts. One grouping serves all of them."""
    calls = {"n": 0}
    real = sd._own

    def counting(*a):
        calls["n"] += 1
        return real(*a)

    monkeypatch.setattr(sd, "_own", counting)
    cookies = [cookie("c", f"h{i}.example{i}.com") for i in range(500)]
    listing, details = sd.views(snapshot(cookies=cookies))
    assert len(listing["sites"]) == 500 and set(details) == {r["site"] for r in listing["sites"]}
    assert calls["n"] <= 4 * len(cookies)
    assert sd.view(snapshot(cookies=cookies)) == listing


# ---- History: the history joined by host -----------------------------------


SECRETS = [
    {"name": "app", "description": "the app", "keys": ["user", "pass"],
     "allowed_urls": ["https://app.example.com"], "restricted": True},
    {"name": "unvisited", "description": "", "keys": ["t"],
     "allowed_urls": ["https://never.example.net"], "restricted": True},
    {"name": "anywhere", "description": "", "keys": ["t"], "allowed_urls": [],
     "restricted": False},
]


def test_the_history_is_one_row_per_host_current_first_with_counts_and_secrets():
    history = [
        visit("https://app.example.com/x", NOW - 60),
        visit("http://app.example.com:8080/dev", NOW - 120),
        visit("https://example.com/", NOW - 3600),
    ]
    data = snapshot(cookies=[cookie("sid", "app.example.com"), cookie("ab", ".example.com")],
                    origins={APP: {"a": "1", "b": "2"}})
    assert sd.history_view(history, data, SECRETS) == {"sites": [
        {"site": "app.example.com", "url": "https://app.example.com/x", "at": NOW - 60,
         "saved": {"cookies": 2, "local": 2, "session": 0},
         "secrets": [{"name": "app", "description": "the app", "keys": ["user", "pass"]}]},
        {"site": "example.com", "url": "https://example.com/", "at": NOW - 3600,
         "saved": None, "secrets": []},
    ]}


def test_a_secret_never_makes_a_row_and_nothing_visited_is_no_rows():
    data = snapshot(cookies=[cookie("t", "never.example.net")])
    assert sd.history_view([], data, SECRETS) == {"sites": []}


def test_a_secret_whose_leash_is_rejected_is_allowed_nowhere():
    # Catalogue.allows refuses the whole secret, its valid lines included, so
    # History must not offer it on the host one of those lines names.
    secrets = [
        {"name": "ok", "allowed_urls": ["https://app.example.com"], "keys": ["k"]},
        {"name": "broken", "allowed_urls": ["https://app.example.com"],
         "allowed_urls_rejected": ["not a url"], "keys": ["k"]},
    ]
    assert [s["name"] for s in sd.matching_secrets(secrets, "app.example.com")] == ["ok"]


def test_a_stored_cookie_without_a_name_or_a_string_domain_counts_as_nothing():
    # The admin list, its event stream and the resources all read the jar: one
    # bad cookie in a stored record must not take them down.
    data = {"cookies": [
        {"name": "ok", "value": "1", "domain": "app.example.com", "path": "/"},
        {"value": "no name", "domain": "app.example.com"},
        {"name": "n", "domain": 5},
        {"name": "", "domain": "app.example.com"},
    ]}
    listing, details = sd.views(data)
    assert [(r["site"], r["cookies"]) for r in listing["sites"]] == [("app.example.com", 1)]
    assert [c["name"] for c in details["app.example.com"]["cookies"]] == ["ok"]


@pytest.mark.parametrize("origin", [5, ["https://app.example.com"], ""])
def test_a_stored_session_storage_origin_that_is_not_a_url_string_counts_as_nothing(origin):
    listing, _ = sd.views({"session": {"origin": origin, "items": {"t": "1"}}})
    assert listing["sites"] == []
