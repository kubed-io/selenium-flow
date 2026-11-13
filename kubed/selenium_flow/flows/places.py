"""Where a flow applies: the sites and paths it declares, and the matcher.

A hint for finding a flow, never a restriction on running one (ruling 8). A
site is a host with an optional port; without a port it matches any (ruling 2),
which is right for finding a flow and wrong for a password's leash, so this is
not `secrets.origin` and shares no code with it (ruling 8). The scheme is
ignored and a site is never a URL (ruling 9). A path is exact, with `*` for one segment
and `**` for any depth, matched with case and without the query (ruling 10). A path
entry may name a hash route after `#`, which is matched as a second path (spec
2026-11-13-hash-routes).
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
    "depth, such as /login or /apps/*/settings. For an app that routes after #, add "
    "the route: /#/auth/* or /app/#/settings/**; an entry without # matches any "
    "route. The query is ignored, and so is any fragment that is not a route. Leave "
    "it out to match any path."
)

SITES_SHAPE = "sites must be a list of sites, such as [localhost:8080, example.com]"
PATHS_SHAPE = "paths must be a list of paths, such as [/login, /admin/login]"


@dataclass(frozen=True)
class Site:
    host: str  # lowercased, IDNA, no brackets; without "*." when wildcard
    port: int | None  # None: any port
    wildcard: bool  # "*.host": any depth of subdomain, never the apex


@dataclass(frozen=True)
class PathEntry:
    path: PathGlob
    route: PathGlob | None  # None: any route (an entry without #)

    def matches(self, path: str, route: str | None) -> bool:
        if not self.path.matches(path):
            return False
        if self.route is None:
            return True
        return route is not None and self.route.matches(route)


@dataclass(frozen=True)
class Places:
    sites: tuple[Site, ...] | None  # None: any site
    paths: tuple[PathEntry, ...] | None  # None: any path

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
            None if paths is None else tuple(_entry(p) for p in paths),
        )

    def applies(self, url: str | None) -> bool:
        key = urls.page_key(url)
        if key is None:
            return False
        host, port, path, route = key
        if self.sites is not None and not any(
            _site_matches(s, host, port) for s in self.sites
        ):
            return False
        return self.paths is None or any(p.matches(path, route) for p in self.paths)


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
    if text.startswith("#"):
        return (
            f"paths: {p} must start with /; write /{text} for a route on the root path"
        )
    if not text.startswith("/"):
        return f"paths: {p} must start with /"
    path, hashed, route = text.partition("#")
    if "#" in route:
        return (
            f"paths: {p} has more than one #; "
            "write one, before the route, as in /#/auth/*"
        )
    bare = path.split("?", 1)[0]
    if hashed and not route.startswith(("/", "!/")):
        return (
            f"paths: {p} has a fragment that is not a route; write {bare}: "
            "only a fragment starting with / or !/ is matched"
        )
    if "?" in path or "?" in route:
        fix = bare + (hashed + route.split("?", 1)[0] if hashed else "")
        return (
            f"paths: {p} has a query; write {fix}: "
            "a path matches whatever query follows it"
        )
    route = route.removeprefix("!")
    if any(
        "**" in seg and seg != "**"
        for seg in path.split("/")[1:] + route.split("/")[1:]
    ):
        return (
            f"paths: {p} has ** inside a segment; "
            "** stands alone between slashes, as in /docs/**"
        )
    if "//" in path or "//" in route:
        return f"paths: {p} has an empty segment (//)"
    return None


ANY_DEPTH = None  # a `**` unit: any number of segments, none included


@dataclass(frozen=True)
class PathGlob:
    """One path pattern, a unit per segment, matched without a regex.

    A unit is one segment's glob, where `*` is any run of characters, or
    `ANY_DEPTH` for `**`. A regex of the same meaning backtracks polynomially
    (`/*a*a*a*a*b` against a long segment took seconds), and a shared flow's
    paths are matched on every workspace's page change, so the matcher is a
    pass over segments and, within one, the greedy star match: O(n·m) each.
    """

    units: tuple[str | None, ...]

    def matches(self, path: str) -> bool:
        """Whether ``path`` (no trailing ``/``, as `urls.page_key` gives) matches."""
        if path and not path.startswith("/"):
            return False
        segments = path.split("/")[1:]
        # The units a prefix of the path can leave the match at, as indices.
        at = self._past_any_depth({0})
        for segment in segments:
            moved = set()
            for i in at:
                if i == len(self.units):
                    continue
                unit = self.units[i]
                if unit is ANY_DEPTH:
                    moved.add(i)  # `**` takes this segment and may take more
                elif _glob(unit, segment):
                    moved.add(i + 1)
            at = self._past_any_depth(moved)
            if not at:
                return False
        return len(self.units) in at

    def _past_any_depth(self, at: set[int]) -> set[int]:
        """``at``, and the unit after each `**` in it, which may take nothing."""
        return at | {
            i + 1 for i in at if i < len(self.units) and self.units[i] is ANY_DEPTH
        }


def _glob(unit: str, segment: str) -> bool:
    """Whether ``segment`` matches ``unit``, where `*` is any run of characters.

    The greedy match: on a mismatch, the last `*` takes one more character."""
    u = s = 0
    star = taken = -1
    while s < len(segment):
        if u < len(unit) and unit[u] == "*":
            star, taken, u = u, s, u + 1
        elif u < len(unit) and unit[u] == segment[s]:
            u, s = u + 1, s + 1
        elif star >= 0:
            taken += 1
            u, s = star + 1, taken
        else:
            return False
    return all(c == "*" for c in unit[u:])


def _path(entry: str) -> PathGlob:
    """The units of a path whose trailing ``/`` is stripped, ``**`` runs as one."""
    units: list[str | None] = []
    for seg in entry.strip().rstrip("/").split("/")[1:]:
        unit = ANY_DEPTH if seg == "**" else seg
        if unit is ANY_DEPTH and units and units[-1] is ANY_DEPTH:
            continue  # `/**/**` is `/**`
        units.append(unit)
    return PathGlob(tuple(units))


def _entry(entry: str) -> PathEntry:
    """A path entry: its path part, and its route part when it has a ``#``."""
    path, hashed, route = entry.strip().partition("#")
    return PathEntry(_path(path), _path(route.removeprefix("!")) if hashed else None)
