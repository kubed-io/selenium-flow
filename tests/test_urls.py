"""The two origin rules agree where it matters and differ where they must."""

import pytest

from kubed.selenium_flow import urls
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
