"""Which stored flows type which secrets — the Secrets tab's backlinks (§F4.10).

A scan of what is on disk, because a step's `args.secret` already names the
secret and the key. Nothing new is recorded: a session typing a secret by hand
with `write` leaves no trace, and recording that would be logging secret use —
its own decision, not a side effect of a page.
"""

from __future__ import annotations

import logging

from ..flows import document as flowdoc
from ..flows import library as flows

log = logging.getLogger(__name__)


def uses(store) -> dict[str, list[dict]]:
    """Secret name -> the flows whose steps type it, with 1-based step numbers."""
    found: dict[str, dict[tuple, dict]] = {}
    if store is None:
        return {}
    for lib in store.sessions():
        try:
            names = store.names(lib)
        except Exception:  # noqa: BLE001 - one unreadable library is not an outage
            log.info("could not list flows in %s", lib)
            continue
        for name in names:
            document = store.get(lib, name)  # a broken file reads as absent
            steps = document.get("steps") if isinstance(document, dict) else None
            ordered = steps if isinstance(steps, list) else []
            for index, step in enumerate(ordered, start=1):
                args = step.get("args") if isinstance(step, dict) else None
                ref = args.get(flowdoc.SECRET_ARG) if isinstance(args, dict) else None
                if not isinstance(ref, dict) or not ref.get("name"):
                    continue
                use = found.setdefault(str(ref["name"]), {}).setdefault(
                    (lib, name),
                    {"session": lib, "flow": name, "steps": [], "keys": [],
                     "shared": lib == flows.GLOBAL_SESSION},
                )
                use["steps"].append(index)
                if ref.get("key") and ref["key"] not in use["keys"]:
                    use["keys"].append(str(ref["key"]))
    def key(use: dict) -> tuple:
        return (use["shared"], use["session"], use["flow"])

    return {secret: sorted(by.values(), key=key) for secret, by in found.items()}
