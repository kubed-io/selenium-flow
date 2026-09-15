"""Every flow in ``flows/``, run by the server against its own admin page.

One test. A new flow is a new case with no code; see ``conftest.py`` before
adding one.
"""

import pytest

from .conftest import ADMIN_ORIGIN, FLOWS, SESSION

# In the module, not the conftest: `pytestmark` in a conftest marks nothing.
pytestmark = pytest.mark.integration

NAMES = sorted(path.stem for path in FLOWS.glob("*.yaml"))


def test_there_are_flows_to_run():
    """Without this the parametrised test passes by having no cases."""
    assert NAMES


@pytest.mark.parametrize("flow", NAMES)
async def test_the_flow_runs(browser, flow):
    result = await browser.call_tool(
        "run_flow",
        {"name": flow, "params": {"admin": ADMIN_ORIGIN, "session": SESSION}},
        raise_on_error=False,
    )
    report = result.structured_content or {}
    assert not result.is_error and report.get("status") == "ok", report or result
