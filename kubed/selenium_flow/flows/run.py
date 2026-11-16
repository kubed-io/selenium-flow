"""Running a saved flow: the entry point over `engine`.

It adds the wiring `flows.api` hands in through `wire` (the tools a step may
name and where a hint points), the default budget and the clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from . import engine, report

# Re-exported: the tests read `listed` through this module.
from .template import listed as listed

# Seconds a whole run may take when the flow does not say. Checked before each
# step: a Selenium call blocks, so a running step is bounded only by its own
# wait_timeout.
RUN_TIMEOUT = 120

_toolbox: engine.Toolbox | None = None


def wire(toolbox: engine.Toolbox, pointer: Callable[[str], str]) -> None:
    """Hand a run the tools a step may name, and how a hint names a page.

    Called once, by `flows.api` as it loads.
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
    bound=None,
    step_done=None,
    in_scope=None,
) -> dict:
    """Run every step of ``document`` against the caller's browser ``session_id``.

    ``after_step``, ``before_step``, ``before_save``, ``bound``, ``step_done``
    and ``in_scope`` are the `engine.Hooks`, each optional; ``stop`` is a
    `threading.Event` that ends the run at the next step or the next poll of a
    waiting one.

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
        hooks=engine.Callbacks(
            before_step, after_step, before_save, bound=bound, step_done=step_done,
            in_scope=in_scope,
        ),
        params=params,
        verbose=verbose,
        timeout=timeout,
        catalogue=catalogue,
        skill_available=skill_available,
        library=library,
        stop=stop,
    )
