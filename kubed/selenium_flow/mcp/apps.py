"""MCP Apps: the admin UI's components, drawn inside a client that can show UI.

`show` returns one resource's JSON and the name of the component that draws it,
and the host paints it in a sandboxed iframe beside the tool call. A client
without the UI extension reads the same JSON as a resource or through
`read_resource`. An MCP client sees only its own workspace, so the workspace
list is never a tool or a resource.

An app runs on a sandbox origin of the host's choosing, so its URLs are
absolute: without PUBLIC_BASE_URL they are paths only something that knows the
host can use.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from kubed.mcp.protocol import UI_EXTENSION_ID, AppConfig, ResourceCSP
from kubed.mcp.request import get_context

from ..http.admin import page as admin_page

log = logging.getLogger(__name__)

RESOURCE_URI = "ui://selenium-flow/component"


def origin(url: str) -> str:
    """The scheme and host of ``url``, as a CSP entry wants it."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def supported() -> bool:
    """Whether this caller's client can render an app.

    Never raises: a context that cannot answer means no.
    """
    try:
        return bool(get_context().client_supports_extension(UI_EXTENSION_ID))
    except Exception:  # noqa: BLE001 - capability probing must not fail a tool
        return False


def config_for(base: str) -> AppConfig:
    """The app declaration a tool carries so a host will render its result.

    The app may call the tool itself (`show`, for drill-down), hence both
    audiences."""
    return AppConfig(
        resource_uri=RESOURCE_URI,
        csp=_csp(base),
        prefers_border=True,
        visibility=["model", "app"],
    )


def _csp(base: str) -> ResourceCSP:
    """What the sandboxed iframe may reach: our own files, framed too, since the
    Lightbox previews a PDF in an <iframe>. The SDK is bundled into the app."""
    own = [d for d in (origin(base),) if d]
    return ResourceCSP(resource_domains=own, connect_domains=own, frame_domains=own)


def available() -> bool:
    """Whether the app shell was built. Without it the tools behave as with
    `mcp.apps` off and still return their data."""
    return admin_page.ui_built("app")


def register(mcp, actions, token: str | None, base: str) -> set[str]:
    """Serve the app shell. Returns the names of any tools it added: none,
    since `show` registers itself (`mcp/show.py`) and only uses the shell.
    """
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
        return admin_page.page("app")

    return set()
