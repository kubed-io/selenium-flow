"""Running a saved flow: every step, server-side, on one clearance.

The entry point, and nothing more. The loop is `engine`, the `${name}` rules are
`template`, keeping a secret out of everything a run says is `redact`, and the
report's lines and hint are `report`. What this adds is the wiring: the tools a
step may name and where a hint points, which `flows.api` hands in through
`wire` so that nothing below it imports the route table or the MCP layer; the
default budget; and the clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from . import engine, report

# Re-exported, the one pair that is: the tests read both through this module.
from .redact import HIDDEN as HIDDEN
from .template import listed as listed

# How long a whole run may take when the flow does not say. Checked between
# steps rather than enforced inside one: a Selenium call blocks, and the honest
# bound is "we will not start another step after this". Each step still has its
# own wait_timeout, and a flow that exists to wait declares its own `timeout`.
#
# Two minutes (Dr K). Longer than nearly any flow needs, so an ordinary one never
# meets it, and short enough that a flow stuck on the wrong page gives up before
# it has wasted much — the one that means to wait says so (§F2.15).
RUN_TIMEOUT = 120

_toolbox: engine.Toolbox | None = None


def wire(toolbox: engine.Toolbox, pointer: Callable[[str], str]) -> None:
    """Hand a run the tools a step may name, and how a hint names a page.

    Called once, by `flows.api` as it loads: it is the module that knows the
    route table and the skill, so the run itself never has to import either.

    A module-level setting for now, because `run()` keeps its signature and is
    called bare. The registry task (the `Capability` table) is where the tools
    are passed per call instead, and this goes.
    """
    global _toolbox
    _toolbox = toolbox
    report.refer_with(pointer)


def run(
    actions,
    document: dict,
    session_id: str,
    params: dict | None = None,
    verbose: bool = False,
    timeout: int | None = None,
    after_step=None,
    catalogue=None,
    skill_available: bool = True,
    library: str = "",
    before_step=None,
    stop=None,
    before_save=None,
) -> dict:
    """Run every step of ``document`` against the browser ``session_id``.

    The browser is the caller's and is resolved before this is called — a flow
    never opens or ends one, which is what lets the same flow run on Chrome and
    then on Firefox unedited (§F1.9).

    ``after_step``, ``before_step`` and ``before_save`` are the three
    `engine.Hooks`, each optional; ``stop`` is a `threading.Event` that ends the
    run at the next step or the next poll of a waiting one.

    ``timeout`` overrides the budget the document declares, which overrides
    `RUN_TIMEOUT`.
    """
    if _toolbox is None:
        raise RuntimeError(
            "flows.run is not wired: flows.api hands it the tools a step may name"
        )
    return engine.execute(
        actions,
        document,
        session_id,
        toolbox=_toolbox,
        # Read through this module, so a test's clock and budget reach the loop.
        clock=time.monotonic,
        default_timeout=RUN_TIMEOUT,
        hooks=engine.Callbacks(before_step, after_step, before_save),
        params=params,
        verbose=verbose,
        timeout=timeout,
        catalogue=catalogue,
        skill_available=skill_available,
        library=library,
        stop=stop,
    )
