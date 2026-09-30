"""Site data: what a session saves for the sites it signed in to, and how
it is shown. Pure logic only; BiDi is Task 2's."""

import json

import pytest

from kubed.selenium_flow.core import site_data as sd
from kubed.selenium_flow.session.store import SessionRecord

pytestmark = pytest.mark.unit

NOW = 1_790_800_000.0


def cookie(name, domain, value="v", http_only=False, expiry=None, secure=True):
    return {
        "name": name, "value": value, "value_type": "string", "domain": domain,
        "path": "/", "http_only": http_only, "secure": secure,
        "same_site": "lax", "expiry": expiry,
    }


def captured(origin="https://app.example.com", local=None, session=None, cookies=None):
    return {
        "cookies": cookies if cookies is not None else [cookie("sid", "app.example.com")],
        "origin": origin, "local": local or {}, "session": session or {},
    }


def test_origin_of_matches_location_origin():
    assert sd.origin_of("https://App.Example.com:443/x?y#z") == "https://app.example.com"
    assert sd.origin_of("http://localhost:3000/a") == "http://localhost:3000"
    assert sd.origin_of("http://x.test:80/") == "http://x.test"
    assert sd.origin_of("about:blank") == ""
    assert sd.origin_of("") == ""


def test_a_save_replaces_cookies_and_adds_its_origin():
    first, _ = sd.merge({}, captured(local={"theme": "dark"}), NOW)
    second, saved = sd.merge(
        first,
        captured(origin="https://sso.example.com", cookies=[cookie("kc", "sso.example.com")]),
        NOW + 5,
    )
    assert [c["name"] for c in second["cookies"]] == ["kc"], "cookies are the whole jar, replaced"
    assert set(second["origins"]) == {"https://app.example.com", "https://sso.example.com"}
    assert second["origins"]["https://app.example.com"]["local"] == {"theme": "dark"}
    assert saved == {"cookies": 1, "sites": ["https://sso.example.com"], "skipped": []}


def test_a_page_with_no_origin_saves_cookies_only():
    data, saved = sd.merge({}, captured(origin=""), NOW)
    assert data["origins"] == {}
    assert saved["sites"] == []


def test_the_restore_marker_is_never_saved():
    data, _ = sd.merge(
        {}, captured(session={"selenium-flow:restored:https://app.example.com": "1", "k": "v"}), NOW
    )
    assert data["origins"]["https://app.example.com"]["session"] == {"k": "v"}


def test_a_save_over_the_cap_keeps_cookies_and_skips_that_storage():
    big = {"blob": "x" * (sd.MAX_BYTES + 1)}
    data, saved = sd.merge({}, captured(local=big), NOW)
    assert data["cookies"] and data["origins"] == {}
    assert saved["skipped"] == [
        {"site": "https://app.example.com", "reason": "storage over 1000000 bytes"}
    ]


def test_merge_keeps_pending():
    data, _ = sd.merge({"pending": {"browser": "b", "origins": [], "script": ""}}, captured(), NOW)
    assert data["pending"]["browser"] == "b"


def test_expired_cookies_are_dropped_and_session_cookies_kept():
    kept = sd.live_cookies(
        [cookie("old", "a.test", expiry=int(NOW) - 1), cookie("new", "a.test", expiry=int(NOW) + 60),
         cookie("session", "a.test", expiry=None)],
        NOW,
    )
    assert [c["name"] for c in kept] == ["new", "session"]


def test_the_preload_script_fills_only_its_own_origin_once_per_tab():
    src = sd.preload_source({"https://app.example.com": {"local": {"a": "1"}, "session": {"b": "2"}}})
    assert src.startswith("() =>")
    assert "location.origin" in src
    assert "selenium-flow:restored:" in src
    assert json.dumps({"a": "1"}) in src


def test_the_view_groups_by_host_and_shares_parent_cookies():
    data, _ = sd.merge(
        {},
        captured(
            local={"theme": "dark"},
            cookies=[
                cookie("sid", "app.example.com", value="secret", http_only=True),
                cookie("ab", ".example.com", value="b1"),
                cookie("kc", "sso.example.com"),
            ],
        ),
        NOW,
    )
    listing = sd.view(data)
    sites = {s["site"]: s for s in listing["sites"]}
    assert set(sites) == {"app.example.com", "sso.example.com"}
    assert sites["app.example.com"]["origin"] == "https://app.example.com"
    assert sites["app.example.com"]["cookies"] == 2, "its own and the shared one"
    assert sites["sso.example.com"]["cookies"] == 2
    assert sites["app.example.com"]["uri"] == "session://site-data/app.example.com"
    one = sd.site_view(data, "app.example.com")
    by_name = {c["name"]: c for c in one["cookies"]}
    assert by_name["sid"]["value"] == sd.MASK, "httpOnly is masked"
    assert by_name["ab"]["value"] == "b1" and by_name["ab"]["shared"] is True
    assert one["local_storage"] == {"theme": "dark"}
    assert sd.site_view(data, "nope.test") is None


def test_a_parent_cookie_with_no_host_gets_its_own_site():
    data, _ = sd.merge({}, captured(origin="", cookies=[cookie("ab", ".example.com")]), NOW)
    assert [s["site"] for s in sd.view(data)["sites"]] == ["example.com"]


def test_secrets_are_matched_by_host_and_unleashed_ones_counted():
    secrets = [
        {"name": "app", "description": "d", "keys": ["username", "password"],
         "allowed_urls": ["https://app.example.com"], "restricted": True},
        {"name": "anywhere", "description": "", "keys": ["token"], "allowed_urls": [],
         "restricted": False},
        {"name": "admin", "description": "a", "keys": ["token"],
         "allowed_urls": ["https://admin.example.com"], "restricted": True},
    ]
    data, _ = sd.merge({}, captured(), NOW)
    listing = sd.view(data, secrets)
    sites = {s["site"]: s for s in listing["sites"]}
    assert sites["app.example.com"]["secrets"] == [
        {"name": "app", "description": "d", "keys": ["username", "password"]}
    ]
    assert sites["admin.example.com"]["saved"] is False, "a secret keeps a row with nothing saved"
    assert listing["unleashed_secrets"] == 1
    assert listing["saved_sites"] == 1


def test_forget_keeps_shared_cookies_and_other_sites():
    data, _ = sd.merge(
        {},
        captured(cookies=[cookie("sid", "app.example.com"), cookie("ab", ".example.com"),
                          cookie("kc", "sso.example.com")]),
        NOW,
    )
    left, removed = sd.forget(data, "app.example.com")
    assert [c["name"] for c in left["cookies"]] == ["ab", "kc"]
    assert left["origins"] == {}
    assert removed == {"site": "app.example.com", "cookies": ["sid"], "origins": ["https://app.example.com"],
                       "kept_shared": ["ab"]}


def test_summary_is_none_when_empty():
    assert sd.summary({}) is None
    data, _ = sd.merge({}, captured(), NOW)
    assert sd.summary(data) == {"sites": 1, "uri": "session://site-data"}


def test_the_record_round_trips_its_site_data():
    data, _ = sd.merge({}, captured(), NOW)
    record = SessionRecord(session_id="s", url="u").with_site_data(data)
    again = SessionRecord.from_json(record.to_json())
    assert again.site_data == data
    assert again.detached().site_data == data, "ending a browser keeps site data"
    assert again.at("https://x").site_data == data


def test_a_record_written_before_site_data_reads_as_empty():
    assert SessionRecord.from_json('{"session_id": "s", "url": "u"}').site_data == {}
    assert SessionRecord.from_json('{"session_id": "s", "site_data": [1]}').site_data == {}
