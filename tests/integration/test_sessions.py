"""Two sessions in one workspace sign in on their own, and one signing out leaves the other in.

Not a flow: a flow runs in one session (FLOW-50), and this needs two. The default
session and a sub-session are two Grid browsers; each signs in to a public demo
site, the sub-session signs out, and the default is still signed in. A shared
cookie jar, or a logout that reached the wrong browser, fails it.

Opens two browsers, and ends both whatever happens.
"""

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from .conftest import TOKEN

pytestmark = pytest.mark.integration

WORKSPACE = "integration-sessions"
SUB = "tester"
SITE = "https://the-internet.herokuapp.com"
# The demo site publishes these on its login page.
USERNAME = "tomsmith"
PASSWORD = "SuperSecretPassword!"


async def _sign_in(client: Client, session: dict) -> None:
    await client.call_tool("navigate", {"url": f"{SITE}/login", **session})
    await client.call_tool(
        "write", {"selector": {"css": "#username"}, "text": USERNAME, **session}
    )
    await client.call_tool(
        "write", {"selector": {"css": "#password"}, "text": PASSWORD, **session}
    )
    await client.call_tool(
        "interact",
        {"action": "click", "selector": {"css": "button[type=submit]"}, **session},
    )
    await client.call_tool(
        "assert",
        {
            "script": "return document.body.innerText.includes('You logged into a secure area!')",
            **session,
        },
    )


# "You logged into a secure area!" is a flash shown once, so a later visit to
# /secure is read for the page's own heading.
async def _secure_page_says(client: Client, session: dict, text: str) -> None:
    """The secure page, read once the page has settled, says ``text``."""
    await client.call_tool("navigate", {"url": f"{SITE}/secure", **session})
    await client.call_tool(
        "assert",
        {
            "script": f"return document.body.innerText.includes({text!r})",
            "message": f"/secure never said {text}",
            **session,
        },
    )


async def test_a_sub_session_signing_out_leaves_the_default_signed_in(server):
    transport = StreamableHttpTransport(
        f"{server}/mcp",
        headers={"Authorization": f"Bearer {TOKEN}", "Sf-Workspace": WORKSPACE},
    )
    default: dict = {}
    sub = {"session": SUB}
    async with Client(transport) as client:
        try:
            await client.call_tool(
                "open_session",
                {"width": 1280, "height": 900, "restore_site_data": False},
            )
            await client.call_tool(
                "open_session", {"width": 1280, "height": 900, "session": SUB}
            )
            await _sign_in(client, default)
            await _sign_in(client, sub)
            await client.call_tool("navigate", {"url": f"{SITE}/logout", **sub})
            await _secure_page_says(
                client, sub, "You must login to view the secure area!"
            )
            await _secure_page_says(client, default, "Welcome to the Secure Area")
        finally:
            await client.call_tool("end_browser", sub, raise_on_error=False)
            await client.call_tool("end_browser", default, raise_on_error=False)
