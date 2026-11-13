"""Where a flow applies: the sites and paths it declares, and the matcher.

A hint for finding a flow, never a restriction on running one (ruling 8). A
site is a host with an optional port; without a port it matches any (ruling 2),
which is right for finding a flow and wrong for a password's leash, so this is
not `secrets.origin` and shares no code with it (ruling 8). The scheme is
ignored and a site is never a URL (ruling 9). A path is exact, with `*` for one segment
and `**` for any depth, matched with case and without the query (ruling 10).
Hosts are IDNA-encoded and a port is explicit or the scheme's default, as the
browser reports them (ruling 23). Plain Python: no selenium, no `secrets`.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from .. import urls

SITES, PATHS = "sites", "paths"

SITES_DESCRIPTION = (
    "Where this flow applies: hosts with an optional port, such as localhost:8080, "
    "example.com or *.example.com. A site with a port matches only that port; one "
    "without matches any. The scheme is ignored. Leave it out to match any site. "
    "A flow with neither sites nor paths is never named in flows_here. This is for "
    "finding a flow, never a restriction: a run is not stopped by it."
)
PATHS_DESCRIPTION = (
    "The paths this flow applies at: exact, with * for one segment and ** for any "
    "depth, such as /login or /apps/*/settings. The query and fragment are ignored. "
    "Leave it out to match any path."
)

SITES_SHAPE = "sites must be a list of sites, such as [localhost:8080, example.com]"
PATHS_SHAPE = "paths must be a list of paths, such as [/login, /admin/login]"


@dataclass(frozen=True)
class Site:
    host: str  # lowercased, IDNA, no brackets; without "*." when wildcard
    port: int | None  # None: any port
    wildcard: bool  # "*.host": any depth of subdomain, never the apex


@dataclass(frozen=True)
class Places:
    sites: tuple[Site, ...] | None  # None: any site
    paths: tuple[re.Pattern[str], ...] | None  # None: any path

    @classmethod
    def parse(cls, document) -> Places | None:
        """The places a flow document declares, None when it declares neither."""
        found = problems(document)
        if found:
            raise ValueError("; ".join(found))
        sites, paths = _declared(document, SITES), _declared(document, PATHS)
        if sites is None and paths is None:
            return None
        return cls(
            None if sites is None else tuple(_site(s) for s in sites),
            None if paths is None else tuple(_path(p) for p in paths),
        )

    def applies(self, url: str | None) -> bool:
        key = urls.page_key(url)
        if key is None:
            return False
        host, port, path = key
        if self.sites is not None and not any(
            _site_matches(s, host, port) for s in self.sites
        ):
            return False
        return self.paths is None or any(p.fullmatch(path) for p in self.paths)


def _site_matches(site: Site, host: str, port: int) -> bool:
    if site.port is not None and site.port != port:
        return False
    return host.endswith("." + site.host) if site.wildcard else host == site.host


def _declared(document, key: str):
    """The list under ``key``, or None when absent (missing or null)."""
    return document.get(key) if isinstance(document, Mapping) else None


def problems(document) -> list[str]:
    """Every reason ``document``'s sites and paths are refused, in the order written."""
    found: list[str] = []
    sites, paths = _declared(document, SITES), _declared(document, PATHS)
    if sites is not None:
        found += _list_problems(
            sites,
            SITES_SHAPE,
            "sites is empty: leave it out to match any site",
            _site_problem,
        )
    if paths is not None:
        found += _list_problems(
            paths,
            PATHS_SHAPE,
            "paths is empty: leave it out to match any path",
            _path_problem,
        )
    return found


def _list_problems(entries, shape: str, empty: str, check) -> list[str]:
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        return [shape]
    if not entries:
        return [empty]
    return [m for m in (check(e) for e in entries) if m]


def _shown(entry: str) -> str:
    """The host, with ``:port`` when written, that a refused site should have been."""
    text = entry.strip()
    if "://" in text or "@" in text:
        try:
            return urlsplit(text if "://" in text else "//" + text).netloc.rpartition(
                "@"
            )[2]
        except ValueError:
            pass
    return re.split(r"[/?#]", text, maxsplit=1)[0]


def _site_problem(entry: str) -> str | None:
    text, s = entry.strip(), repr(entry)
    if "://" in text:
        return (
            f"sites: {s} has a scheme; write {_shown(entry)}: "
            "a site matches http and https alike"
        )
    if "@" in text:
        return f"sites: {s} has a user name; write {_shown(entry)}"
    if re.search(r"[/?#]", text):
        return (
            f"sites: {s} has a path; write {_shown(entry)}, and put the path in paths"
        )
    if text == "*":
        return "sites: '*' matches every site; leave sites out to match any site"
    if "*" in text.removeprefix("*."):
        return (
            f"sites: {s} has a * that is not at the front; "
            "write *. in front only, as in *.example.com"
        )
    parsed = _site_parts(text.removeprefix("*."))
    if parsed is None:
        return f"sites: {s} is not a host name"
    if parsed[1] is not None and not 1 <= parsed[1] <= 65535:
        return f"sites: {s} has port {parsed[1]}; a port is a number from 1 to 65535"
    return None


def _site_parts(text: str) -> tuple[str, int | None] | None:
    """(host, port) of a bare ``host[:port]``, or None when it is not a host name.

    A port outside 1-65535 is returned as written; the caller says so."""
    if text.startswith("["):
        host, closed, rest = text[1:].partition("]")
        if not closed:
            return None
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        if rest and not rest.startswith(":"):
            return None
        port_text = rest[1:] if rest else None
        host = host.lower()
    else:
        host, _, port_text = text.partition(":")
        if ":" in port_text:
            return None
        port_text = port_text if ":" in text else None
        host = host.lower()
        if not host:
            return None
        try:
            if not host.isascii():
                host = host.encode("idna").decode("ascii")
            else:
                host.encode("idna")
        except UnicodeError:
            return None
    port = None
    if port_text is not None:
        if not port_text.isascii() or not port_text.isdigit():
            return None
        port = int(port_text)
    return host, port


def _site(entry: str) -> Site:
    text = entry.strip()
    wildcard = text.startswith("*.")
    host, port = _site_parts(text.removeprefix("*."))  # type: ignore[misc]
    return Site(host, port, wildcard)


def _path_problem(entry: str) -> str | None:
    text, p = entry.strip(), repr(entry)
    if not text.startswith("/"):
        return f"paths: {p} must start with /"
    if re.search(r"[?#]", text):
        bare = re.split(r"[?#]", text, maxsplit=1)[0]
        return (
            f"paths: {p} has a query or fragment; write {bare}: "
            "a path matches whatever query follows it"
        )
    segments = text.split("/")[1:]
    if any("**" in seg and seg != "**" for seg in segments):
        return (
            f"paths: {p} has ** inside a segment; "
            "** stands alone between slashes, as in /docs/**"
        )
    if "//" in text:
        return f"paths: {p} has an empty segment (//)"
    return None


def _path(entry: str) -> re.Pattern[str]:
    """One anchored regex over a path whose trailing ``/`` is stripped."""
    out = ""
    for seg in entry.strip().rstrip("/").split("/")[1:]:
        if seg == "**":
            out += "(?:/[^/]*)*"
        else:
            out += "/" + "[^/]*".join(re.escape(part) for part in seg.split("*"))
    return re.compile(out)
