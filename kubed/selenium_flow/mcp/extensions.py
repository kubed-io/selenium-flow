"""What the extensions need beside FastMCP, under mcp-kb's names.

``AdvertiseLegacyExtensions`` is shared by ``skill`` and ``agents``, which
import it from here, not from one another, so either can be deleted alone.
``complete`` is the Agents extension's envelope.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping

from fastmcp.server.middleware import Middleware


def complete(**fields: object) -> dict:
    """An extension result in the Agents extension's envelope.

    FastMCP's Skills results carry the same fields.

    mcp-kb's freshness fields: a hint, not an integrity property.
    """
    return {"resultType": "complete", "ttlMs": 0, "cacheScope": "private", **fields}


class AdvertiseLegacyExtensions(Middleware):
    """Declare extensions in a legacy ``initialize`` reply too.

    FastMCP declares registered extensions in ``server/discover`` but drops
    ``capabilities.extensions`` from ``initialize``, and still answers the
    methods; without this, a handshake-era client never learns they exist.
    Each is declared with the value ``server/discover`` gives it, beside any
    already there.
    """

    def __init__(self, declared: Mapping[str, dict]):
        self._declared = dict(declared)

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is not None:
            result.capabilities.extensions = {
                **(result.capabilities.extensions or {}),
                **copy.deepcopy(self._declared),
            }
        return result
