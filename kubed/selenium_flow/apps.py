"""MCP Apps: the shared components, rendered inside a client that can show UI.

An app here is one component, not a dashboard. A tool returns the data for a
single view — the files this session downloaded — and the host paints it in a
sandboxed iframe next to the tool call. The admin page composes those same
components into something a person browses; this is the other end of the same
library.

**Nothing here lists other people's sessions.** An MCP client sees its own
session and nothing else, so the session list is an admin view over HTTP and
never a tool or a resource. See AGENTS.md, "A client owns one session".

Support is uneven and the degradation is the interesting part. Everything
returns ordinary structured data with absolute, signed URLs in it, and the
``component`` field is only a hint about how to draw it. So:

- a client with the UI extension renders the component,
- a client without it still gets the URLs, and can link or embed them,
- a client that shows neither still gets the filenames and sizes as text.

Which is the same shape as the resource/tool mirroring in ``resources.py``: one
server, and the client's declared capabilities decide the rendering.

URLs handed out here are absolute. An app runs on a sandbox origin of the host's
choosing, so a relative path would resolve against something that is not this
server. PUBLIC_BASE_URL is what makes them resolvable, and without it the links
are still correct paths — just only usable by something that already knows the
host.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from fastmcp.apps import UI_EXTENSION_ID, AppConfig, ResourceCSP
from fastmcp.server.dependencies import get_context

from . import admin

log = logging.getLogger(__name__)

RESOURCE_URI = "ui://selenium-flow/component"
# The SDK that talks to the host. Apps get a deny-by-default CSP — no network at
# all — so both this and our own origin have to be declared below.
SDK_ORIGIN = "https://unpkg.com"


def enabled(env: dict | None = None) -> bool:
    """Whether to offer apps at all. On unless explicitly turned off."""
    env = os.environ if env is None else env
    return str(env.get("APPS_ENABLED", "true")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def public_base(env: dict | None = None) -> str:
    """The externally reachable root of this server, or "" if it has none."""
    env = os.environ if env is None else env
    return str(env.get("PUBLIC_BASE_URL", "")).strip().rstrip("/")


def origin(url: str) -> str:
    """Just the scheme and host of ``url`` — what a CSP entry wants."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def supported() -> bool:
    """Whether this caller's client can render an app.

    Never raises: a context that cannot answer means no, and the caller falls
    back to data. Being wrong here should cost a nicer rendering, never a call.
    """
    try:
        return bool(get_context().client_supports_extension(UI_EXTENSION_ID))
    except Exception:  # noqa: BLE001 - capability probing must not fail a tool
        return False


def config_for(base: str) -> AppConfig:
    """The app declaration a tool carries so a host will render its result."""
    return AppConfig(resource_uri=RESOURCE_URI, csp=_csp(base), prefers_border=True)


def _csp(base: str) -> ResourceCSP:
    """What the sandboxed iframe is allowed to reach: our files, and the SDK."""
    return ResourceCSP(
        resource_domains=[d for d in (origin(base), SDK_ORIGIN) if d],
        connect_domains=[d for d in (origin(base),) if d],
    )


def register(mcp, actions, token: str | None) -> set[str]:
    """Serve the app shell. Returns the names of any tools it added.

    Empty today: the only tool that lived here listed every browser on the Grid,
    which is precisely what an MCP client must not be able to see. The shell
    stays, because the files component is rendered through it.
    """
    base = public_base()
    csp = _csp(base)

    @mcp.resource(
        RESOURCE_URI,
        name="selenium-flow component",
        description="Renders one selenium-flow component from a tool result.",
        # No mime_type: FastMCP stamps ui:// resources with
        # text/html;profile=mcp-app, which is what marks it as an app. Setting
        # a plain text/html here overrides that and the host ignores it.
        app=AppConfig(csp=csp),
    )
    def component_app() -> str:
        return admin.page("app.html")

    return set()
