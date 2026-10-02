"""The admin page itself: the built UI's shell, and where it is found.

The UI is built by ``npm --prefix ui run build`` into ``http/static`` — beside
this package, not in it — so a checkout and an installed wheel look in the same
place, and an install that skipped the build simply has none (§F4.17).
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

STATIC_DIR = "static"

# What the UI's URL answers when no UI was built (§F4.17): the server is whole
# without it, and says only its name.
PLACEHOLDER = (
    "<!doctype html>\n"
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>Selenium Flow</title>\n"
    "<h1>Selenium Flow</h1>\n"
)

# What the Grid console is reachable at *from a browser*. Behind the shared
# ingress both halves sit on one host, so the console is simply the root; the
# default says so, and an env var covers any other arrangement.
DEFAULT_CONSOLE_URL = "/"


def static_path() -> Path:
    """The built UI, beside this package.

    `npm --prefix ui run build` writes it here — gitignored, never committed
    (§F4.15) — so a checkout and an installed wheel look in the same place, and
    an install that skipped the build simply has none (§F4.17).
    """
    return Path(__file__).parent.parent / STATIC_DIR


def ui_built(name: str) -> bool:
    """Whether surface ``name`` ("admin" or "app") has all three of its files."""
    folder = static_path()
    return all((folder / f"{name}.{ext}").is_file() for ext in ("html", "css", "js"))


def read(name: str) -> str:
    """One file from the built UI."""
    return (static_path() / name).read_text(encoding="utf-8")


def page(name: str, **substitutions: str) -> str:
    """Surface ``name``'s shell with its bundle inlined and ``__NAME__`` filled.

    Deliberately not a template engine. The shell's own comments go first:
    they name the placeholders, and a comment filled with the bundle is a
    second copy of it that any ``-->`` inside ends early. Everything else is
    filled in a single pass over the shell — the bundle, and the
    substitutions — so nothing inside the bundle is ever substituted, and a
    substitution's own value (say a mount of ``/__JS__``) is never rescanned
    as a placeholder and handed the bundle in its place. A literal
    ``</script`` in the JS is escaped so it cannot end the inline script
    early. The substitutions land in attribute values, so they are
    HTML-escaped, quotes included.
    """
    html = re.sub(r"<!--.*?-->\n?", "", read(f"{name}.html"), flags=re.DOTALL)
    bundle = {
        "CSS": read(f"{name}.css"),
        "JS": re.sub(r"</(script)", r"<\\/\1", read(f"{name}.js"), flags=re.IGNORECASE),
    }

    def fill(match: re.Match) -> str:
        key = match.group(1)
        if key in bundle:
            return bundle[key]
        if key in substitutions:
            return escape(substitutions[key], quote=True)
        return match.group(0)

    return re.sub(r"__([A-Z]+)__", fill, html)


def mount(mcp, prefix: str, console_url: str | None) -> None:
    """The page at the root of wherever this server is mounted, and the old
    ``/admin`` URL that now redirects to it."""
    console = console_url or DEFAULT_CONSOLE_URL

    @mcp.custom_route(f"{prefix}/", methods=["GET"], name="admin_ui")
    async def admin_ui(_request: Request) -> HTMLResponse:
        """The page itself, at the root of wherever this server is mounted.

        The UI is what a person gets for visiting the server; `/admin/*` is the
        API that page calls, which is a different thing wearing a similar name.
        The root used to 404, so there was no landing page at all.

        Unauthenticated on purpose — it is the sign-in form, and every byte of
        data it shows is fetched separately with the token. Without a build it
        is the placeholder (§F4.17).
        """
        if not ui_built("admin"):
            return HTMLResponse(PLACEHOLDER)
        return HTMLResponse(page("admin", CONSOLE=console, MOUNT=prefix))

    @mcp.custom_route(f"{prefix}/admin", methods=["GET"], name="admin_ui_moved")
    async def admin_ui_moved(_request: Request) -> Response:
        """Where the page used to be. A redirect rather than a second copy, so
        there is one URL for the UI and one answer to "where is it".

        Relative, because `/flow/admin` may be `/base/flow/admin` to the browser
        behind an ingress that stripped `/base`; `./` lands on the UI either way.
        """
        return RedirectResponse("./", status_code=301)
