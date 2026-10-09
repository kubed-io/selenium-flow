"""The admin page itself: the built UI's shell, and where it is found.

The UI is built by ``npm --prefix ui run build`` into ``http/static`` — beside
this package, not in it — so a checkout and an installed wheel look in the same
place, and an install that skipped the build simply has none (§F4.17).
"""

from __future__ import annotations

import hashlib
import json
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

# Sent with the page, placeholder included. A page that can drive every browser
# is not one to let any site frame (clickjacking), so framing is off unless an
# origin is listed in `security.frame_ancestors`.
def security_headers(frame_ancestors: list[str] | None = None) -> dict[str, str]:
    allowed = " ".join(frame_ancestors or []) or "'none'"
    return {
        "Content-Security-Policy": f"frame-ancestors {allowed}",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
    }


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


# (name, substitutions, the built files' (mtime, size)) -> (html, etag). The files
# are in the key, so a rebuilt UI is a new entry rather than a stale hit.
_pages: dict[tuple, tuple[str, str]] = {}


def _stamp(name: str) -> tuple:
    folder = static_path()
    stamps = []
    for ext in ("html", "css", "js"):
        info = (folder / f"{name}.{ext}").stat()
        stamps.append((info.st_mtime_ns, info.st_size))
    return tuple(stamps)


def page_with_etag(name: str, **substitutions: str) -> tuple[str, str]:
    """:func:`page`'s answer and a strong ETag for it, built once per
    (name, substitutions) and per version of the built files."""
    key = (name, tuple(sorted(substitutions.items())), _stamp(name))
    built = _pages.get(key)
    if built is None:
        if len(_pages) >= 16:
            _pages.clear()
        html = _build(name, **substitutions)
        digest = hashlib.sha256(html.encode()).hexdigest()[:32]
        # Held in a local: a concurrent clear() may empty the cache between the
        # store and the return, and re-reading `_pages[key]` would raise.
        built = (html, f'"{digest}"')
        _pages[key] = built
    return built


def page(name: str, **substitutions: str) -> str:
    """Surface ``name``'s shell, built once per (name, substitutions) and
    rebuilt when any of its three files changes. See :func:`_build`."""
    return page_with_etag(name, **substitutions)[0]


def _build(name: str, **substitutions: str) -> str:
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


def sign_in(oidc) -> str:
    """What the page needs to offer Sign in with OIDC, or "" when it should not.

    The issuer and the public client id, and nothing else: the audience, the
    JWKS URI and the roles are the server's business (spec
    2026-10-09-admin-oidc §3). Both values are public by nature.
    """
    if not oidc.client_id:
        return ""
    return json.dumps({"issuer": oidc.issuer, "client_id": oidc.client_id})


def _matches(header: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` names ``etag`` (weak validators compare
    equal for a GET, and ``*`` matches anything)."""
    if not header:
        return False
    tags = [t.strip().removeprefix("W/") for t in header.split(",")]
    return "*" in tags or etag in tags


def mount(
    mcp,
    prefix: str,
    console_url: str | None,
    frame_ancestors: list[str] | None = None,
    oidc: str = "",
) -> None:
    """The page at the root of wherever this server is mounted, and the old
    ``/admin`` URL that now redirects to it."""
    console = console_url or DEFAULT_CONSOLE_URL
    secure = security_headers(frame_ancestors)

    @mcp.custom_route(f"{prefix}/", methods=["GET"], name="admin_ui")
    async def admin_ui(request: Request) -> Response:
        """The page itself, at the root of wherever this server is mounted.

        The UI is what a person gets for visiting the server; `/admin/*` is the
        API that page calls, which is a different thing wearing a similar name.
        The root used to 404, so there was no landing page at all.

        Unauthenticated on purpose — it is the sign-in form, and every byte of
        data it shows is fetched separately with the token. Without a build it
        is the placeholder (§F4.17).
        """
        if not ui_built("admin"):
            return HTMLResponse(PLACEHOLDER, headers=secure)
        html, etag = page_with_etag(
            "admin", CONSOLE=console, MOUNT=prefix, OIDC=oidc
        )
        # no-cache is "ask every time", and the ETag makes the ask cheap: a
        # rebuilt UI is picked up on the next load, an unchanged one is a 304.
        headers = {"Cache-Control": "no-cache", "ETag": etag, **secure}
        if _matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return HTMLResponse(html, headers=headers)

    @mcp.custom_route(f"{prefix}/admin", methods=["GET"], name="admin_ui_moved")
    async def admin_ui_moved(_request: Request) -> Response:
        """Where the page used to be. A redirect rather than a second copy, so
        there is one URL for the UI and one answer to "where is it".

        Relative, because `/flow/admin` may be `/base/flow/admin` to the browser
        behind an ingress that stripped `/base`; `./` lands on the UI either way.
        """
        return RedirectResponse("./", status_code=301)
