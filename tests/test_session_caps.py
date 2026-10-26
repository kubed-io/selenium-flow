"""The capabilities a session sends, and where each one came from.

`core/session_caps.build` is the one place the precedence rules live (spec
2026-10-26-session-capabilities, *Building the sent set*): flow < option <
config < raw, owned keys refused, vendor blocks merged with ours winning. Every
row of the spec's two tables has a test here.
"""

import copy
import re

import pytest

from kubed.selenium_flow.core.session_caps import (
    OWNED,
    SOURCES,
    VENDOR,
    build,
    check,
)

pytestmark = pytest.mark.unit


def source(built, key):
    """The sources the sent set records for ``key``."""
    return next(e["sources"] for e in built.sent if e["key"] == key)


def test_the_sources_and_vendor_blocks_are_the_four_and_the_two():
    assert SOURCES == ("flow", "option", "config", "raw")
    assert VENDOR == {"chrome": "goog:chromeOptions", "firefox": "moz:firefoxOptions"}


# ---- flow -------------------------------------------------------------------


def test_flow_sends_its_three_keys_and_its_block_on_both_browsers():
    for name in ("chrome", "firefox"):
        built = build(name)
        assert built.send["unhandledPromptBehavior"] == "ignore"
        assert built.send["se:downloadsEnabled"] is True
        assert built.send["webSocketUrl"] is True
        assert built.send["browserName"] == name
        assert source(built, "unhandledPromptBehavior") == ["flow"]
        assert source(built, VENDOR[name]) == ["flow"]
    chrome = build("chrome").send["goog:chromeOptions"]
    assert chrome["args"] == ["--no-sandbox", "--disable-dev-shm-usage"]
    assert chrome["prefs"] == {
        "profile.default_content_setting_values.automatic_downloads": 1,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.password_manager_leak_detection": False,
    }
    firefox = build("firefox").send["moz:firefoxOptions"]
    assert "args" not in firefox
    assert firefox["prefs"] == {
        "browser.download.folderList": 2,
        "browser.download.useDownloadDir": True,
        "browser.helperApps.neverAsk.saveToDisk": (
            "application/pdf,image/png,application/octet-stream"
        ),
        "pdfjs.disabled": True,
        "signon.rememberSignons": False,
    }


def test_no_browser_means_the_default_one():
    assert build(None).browser == "chrome"
    assert build(None).send["browserName"] == "chrome"


def test_an_unknown_browser_is_refused():
    with pytest.raises(ValueError, match="unknown browser"):
        build("safari")


def test_nothing_selenium_adds_on_its_own_is_sent():  # ruling 23
    for name in ("chrome", "firefox"):
        send = build(name).send
        assert not {"pageLoadStrategy", "moz:debuggerAddress"} & send.keys()


# ---- option -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["chrome", "firefox"])
def test_insecure_always_sends_accept_insecure_certs(name):  # ruling 23, Dr K
    assert build(name).send["acceptInsecureCerts"] is False
    assert build(name, insecure=False).send["acceptInsecureCerts"] is False
    assert build(name, insecure=True).send["acceptInsecureCerts"] is True
    assert source(build(name), "acceptInsecureCerts") == ["option"]


def test_options_produce_their_keys_with_the_option_source():
    built = build("firefox", version="142", insecure=True, record=True, video_name="w")
    assert built.send["browserVersion"] == "142"
    assert built.send["se:recordVideo"] is True and built.send["se:videoName"] == "w"
    assert built.version == "142"
    for key in ("browserName", "browserVersion", "se:recordVideo", "se:videoName"):
        assert source(built, key) == ["option"]


def test_record_off_sends_no_video_keys():
    send = build("chrome", video_name="w").send
    assert "se:recordVideo" not in send and "se:videoName" not in send
    assert "se:videoName" not in build("chrome", record=True).send


@pytest.mark.parametrize("asked", ["latest", "Latest", " LATEST ", "", "  "])
def test_latest_and_no_version_send_no_browser_version(asked):  # ruling 19
    assert "browserVersion" not in build("chrome", version=asked).send
    assert build("chrome", version=asked).version is None
    assert "browserVersion" not in build("chrome").send


def test_a_version_is_sent_stripped():
    assert build("chrome", version=" 152 ").send["browserVersion"] == "152"


def test_an_option_arg_joins_the_browsers_own_block():  # ruling 29, for #86
    built = build("chrome", option_args=["--headless=new"])
    assert built.send["goog:chromeOptions"]["args"] == [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--headless=new",
    ]
    assert source(built, "goog:chromeOptions") == ["flow", "option"]
    firefox = build("firefox", option_args=["-headless"])
    assert firefox.send["moz:firefoxOptions"]["args"] == ["-headless"]
    assert source(firefox, "moz:firefoxOptions") == ["flow", "option"]


# ---- config and raw ---------------------------------------------------------


def test_config_keys_are_sent_with_the_config_source():
    built = build("chrome", config={"kubed:pool": "default"})
    assert built.send["kubed:pool"] == "default"
    assert source(built, "kubed:pool") == ["config"]
    assert built.raw_keys == ()


def test_raw_overrides_config_and_reads_raw():  # ruling 14
    built = build(
        "chrome", config={"kubed:pool": "default"}, raw={"kubed:pool": "headless"}
    )
    assert built.send["kubed:pool"] == "headless"
    assert source(built, "kubed:pool") == ["raw"]
    assert built.raw_keys == ("kubed:pool",)


def test_a_raw_key_the_config_does_not_set_is_sent_as_raw():
    built = build("chrome", config={"a:b": 1}, raw={"c:d": [1, {"e": None}]})
    assert built.send["a:b"] == 1 and built.send["c:d"] == [1, {"e": None}]
    assert source(built, "c:d") == ["raw"]


@pytest.mark.parametrize("key", sorted(OWNED))
def test_an_owned_raw_key_is_refused_naming_what_controls_it(key):  # ruling 15
    wanted = f"{key} cannot be sent in capabilities: {OWNED[key]}"
    with pytest.raises(ValueError, match=re.escape(wanted)):
        build("chrome", raw={key: True})


@pytest.mark.parametrize("key", sorted(OWNED))
def test_an_owned_config_key_is_refused_naming_the_setting(key):
    wanted = f"{key} cannot be sent in session.capabilities: {OWNED[key]}"
    with pytest.raises(ValueError, match=re.escape(wanted)):
        build("chrome", config={key: True})


def test_the_refusals_name_the_options():
    assert OWNED["browserName"] == "use browser= instead"
    assert OWNED["browserVersion"] == "use version= instead"
    assert OWNED["acceptInsecureCerts"] == "use insecure=true or insecure=false instead"
    assert OWNED["se:recordVideo"] == OWNED["se:videoName"] == "use record=true instead"
    assert OWNED["timeouts"] == "use page_load_timeout= and script_timeout= instead"
    assert OWNED["unhandledPromptBehavior"] == (
        "selenium-flow keeps dialogs open for the dialog tool"
    )
    assert OWNED["se:downloadsEnabled"] == "the downloads folder needs it"
    assert OWNED["webSocketUrl"] == "site data, capture and the monitor need it"


def test_a_remembered_owned_key_says_how_to_clear_it():  # ruling 26
    with pytest.raises(ValueError, match=re.escape("open_session(capabilities={})")):
        check({"browserName": "chrome"}, remembered=True)
    with pytest.raises(ValueError) as plain:
        check({"browserName": "chrome"})
    assert "open_session(capabilities={})" not in str(plain.value)


def test_nothing_raw_is_fine():
    check(None)
    check({})
    check({"kubed:pool": "x", "goog:chromeOptions": {"args": ["--a"], "prefs": {}}})


def test_a_raw_object_that_is_not_one_is_refused():
    with pytest.raises(ValueError, match="capabilities must be an object"):
        check(["browserName"])


# ---- the vendor block merge -------------------------------------------------


def test_vendor_args_merge_ours_first_without_duplicates():  # ruling 5
    built = build(
        "chrome",
        option_args=["--x"],
        config={"goog:chromeOptions": {"args": ["--y", "--no-sandbox"]}},
        raw={"goog:chromeOptions": {"args": ["--z", "--y"]}},
    )
    assert built.send["goog:chromeOptions"]["args"] == [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--x",
        "--y",
        "--z",
    ]
    assert source(built, "goog:chromeOptions") == ["flow", "option", "config", "raw"]
    assert built.ignored == []


def test_vendor_prefs_merge_flow_wins_and_the_loss_is_listed():  # ruling 28
    built = build(
        "chrome",
        config={"goog:chromeOptions": {"prefs": {"a": 1}}},
        raw={
            "goog:chromeOptions": {
                "prefs": {
                    "a": 2,
                    "credentials_enable_service": True,
                    "profile.password_manager_enabled": False,
                }
            }
        },
    )
    prefs = built.send["goog:chromeOptions"]["prefs"]
    assert prefs["a"] == 2 and prefs["credentials_enable_service"] is False
    assert built.ignored == ["goog:chromeOptions.prefs.credentials_enable_service"]


def test_a_config_pref_ours_beats_is_listed_too():
    built = build(
        "firefox", config={"moz:firefoxOptions": {"prefs": {"pdfjs.disabled": False}}}
    )
    assert built.send["moz:firefoxOptions"]["prefs"]["pdfjs.disabled"] is True
    assert built.ignored == ["moz:firefoxOptions.prefs.pdfjs.disabled"]


def test_other_block_keys_come_from_config_then_raw():
    built = build(
        "chrome",
        config={"goog:chromeOptions": {"binary": "/a", "log": {"level": "x"}}},
        raw={"goog:chromeOptions": {"binary": "/b"}},
    )
    block = built.send["goog:chromeOptions"]
    assert block["binary"] == "/b" and block["log"] == {"level": "x"}
    assert source(built, "goog:chromeOptions") == ["flow", "config", "raw"]


def test_the_other_browsers_block_is_sent_untouched():
    built = build("chrome", raw={"moz:firefoxOptions": {"args": ["-foo"]}})
    assert built.send["moz:firefoxOptions"] == {"args": ["-foo"]}
    assert source(built, "moz:firefoxOptions") == ["raw"]
    assert built.send["goog:chromeOptions"]["args"] == [
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]


@pytest.mark.parametrize(
    "block,said",
    [
        ({"args": "--lang=fr"}, "goog:chromeOptions.args in capabilities must be a list of strings"),
        ({"args": [1]}, "goog:chromeOptions.args in capabilities must be a list of strings"),
        ({"prefs": []}, "goog:chromeOptions.prefs in capabilities must be an object"),
    ],
)
def test_a_vendor_block_of_the_wrong_shape_is_refused(block, said):  # ruling 27
    with pytest.raises(ValueError, match=re.escape(said)):
        build("firefox", raw={"goog:chromeOptions": block})


def test_a_vendor_block_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError, match="moz:firefoxOptions in capabilities must be an object"):
        build("firefox", raw={"moz:firefoxOptions": "x"})


# ---- what is recorded -------------------------------------------------------


def test_sent_follows_the_design_order():
    keys = [e["key"] for e in build("firefox", version="142", config={"kubed:pool": "d"}).sent]
    assert keys == [
        "browserName",
        "browserVersion",
        "acceptInsecureCerts",
        "kubed:pool",
        "moz:firefoxOptions",
        "unhandledPromptBehavior",
        "se:downloadsEnabled",
        "webSocketUrl",
    ]


def test_sent_and_send_say_the_same_thing():
    built = build(
        "chrome",
        version="1",
        record=True,
        video_name="v",
        config={"kubed:pool": "d", "goog:chromeOptions": {"args": ["--c"]}},
        raw={"x:y": {"z": 1}},
    )
    assert {e["key"]: e["value"] for e in built.sent} == built.send
    assert list(built.send) == [e["key"] for e in built.sent]
    for entry in built.sent:
        assert entry["sources"] and set(entry["sources"]) <= set(SOURCES)


def test_build_never_mutates_its_inputs():
    config = {"goog:chromeOptions": {"args": ["--y"], "prefs": {"b": 1}}, "k:v": [1]}
    raw = {"goog:chromeOptions": {"prefs": {"a": 1}}, "r:s": {"t": [2]}}
    config_before, raw_before = copy.deepcopy(config), copy.deepcopy(raw)
    first = build("chrome", config=config, raw=raw)
    build("chrome", config=config, raw=raw)
    assert config == config_before and raw == raw_before
    # Nothing sent shares an object with an input, so a later change to the
    # sent set cannot reach the config or a stored record.
    first.send["goog:chromeOptions"]["args"].append("--later")
    first.send["k:v"].append(2)
    assert config == config_before and raw == raw_before
