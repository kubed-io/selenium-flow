"""The kernel imports no protocol library and, below it, no driver.

`AGENTS.md` shapes the package in layers: the protocol (`mcp/`, `http/`) sits on
top, Selenium on the bottom (`core/browser`, `core/actions`, `core/pointer`,
`core/probe`), and what lies between -- the flow engine, the session store, the
site-data snapshot, the vocabulary modules -- is plain Python. A promise nothing
checks drifts, so this blocks the libraries in a fresh subprocess and imports
every module that is meant to be free of them, found by walking the tree so a
module added later is covered without being listed.

Two boundaries, asserted as they are today:

* with `fastmcp`, `starlette`, `mcp` and `uvicorn` blocked, everything outside
  the protocol layers imports (`flows/api` is the flow tools' HTTP face and is
  protocol by nature);
* with `selenium` blocked as well, the modules in `NO_SELENIUM` import.

Each exception is named in `SELENIUM_EXCEPTIONS` with the reason, and a test
proves every one still fails, so tightening the boundary means deleting a line
here rather than remembering to.
"""

import json
import pkgutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).parent.parent
PACKAGE = REPO / "kubed" / "selenium_flow"

PROTOCOL = ("fastmcp", "starlette", "mcp", "uvicorn")


def walk(directory: str, skip: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Dotted names of the modules in one package directory, found on disk.

    Importing the package here would run the very imports the subprocess exists
    to block, so the tree is walked, not imported.
    """
    base = PACKAGE / directory
    return tuple(
        f"kubed.selenium_flow.{directory}.{module.name}"
        for module in pkgutil.iter_modules([str(base)])
        if module.name not in skip
    )


# Layer 1: no protocol library anywhere below the protocol layers. `core/browser`,
# `pointer`, `probe` and `spare` may import selenium, which is not under test
# here; `flows/api` is the HTTP face of the flow tools and imports starlette.
NO_PROTOCOL = (
    *walk("core"),
    *walk("session"),
    *walk("flows", skip=("api",)),
    *walk("site_data"),
    *(
        f"kubed.selenium_flow.{name}"
        for name in ("names", "urls", "errors", "secrets", "config")
    ),
)

# Layer 2: these also import with selenium blocked.
NO_SELENIUM = tuple(
    f"kubed.selenium_flow.{name}"
    for name in (
        "core.defaults",
        "core.naming",
        "core.coerce",
        "site_data.snapshot",
        "flows.template",
        "flows.redact",
        "flows.engine",
        "flows.report",
        "names",
        "urls",
        "config",
        "secrets",
    )
)

# Modules that belong in the selenium-free set but are not yet, each with the
# reason. `test_every_selenium_exception_still_fails` fails the moment one
# starts importing clean, so the entry has to go with the fix.
SELENIUM_EXCEPTIONS = {
    # Builds its table by walking Selenium's `Keys` class, so it follows the
    # installed Selenium. A literal table would freeze today's keys; the one
    # import is the point of the module.
    "kubed.selenium_flow.core.keys": "table derived from selenium Keys",
    # Imports the Selenium exception classes to say which are transient, which
    # are the caller's fault and which mean the session is gone. `assertion` and
    # `session/store` reach it for one class or a helper each. Task 15 splits
    # the Selenium-dependent classification from the plain exceptions and
    # `message`/`formatted` so these three drop their edge.
    "kubed.selenium_flow.errors": "classification tuples are selenium exceptions",
    "kubed.selenium_flow.core.assertion": "imports errors.AssertionFailed",
    "kubed.selenium_flow.session.store": "imports errors",
}

SCRIPT = textwrap.dedent(
    """
    import importlib
    import json
    import sys

    sys.path.insert(0, {repo!r})

    class BlockedImport(ImportError):
        pass

    class _Blocked:
        \"\"\"Raises the instant anything below imports a blocked library.\"\"\"

        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in {blocked!r}:
                raise BlockedImport(f"{{name}} import blocked")
            return None

    sys.meta_path.insert(0, _Blocked())

    blocked, broken = {{}}, {{}}
    for name in {modules!r}:
        try:
            importlib.import_module(name)
        except BlockedImport as exc:
            blocked[name] = str(exc)
        except ImportError as exc:
            broken[name] = str(exc)
    print(json.dumps({{"blocked": blocked, "broken": broken}}))
    """
)


def import_failures(modules, blocked) -> dict[str, str]:
    """Module -> the blocked import that stopped it, for each that was stopped."""
    script = SCRIPT.format(repo=str(REPO), modules=tuple(modules), blocked=blocked)
    # Inherit the parent's `-S`: where site-packages carries another `kubed`
    # package it would shadow this namespace and every import would 404.
    flags = ["-S"] if sys.flags.no_site else []
    result = subprocess.run(
        [sys.executable, *flags, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout.strip().splitlines()[-1])
    # An ImportError that is not the blocker's is the test environment (a
    # dependency missing in the subprocess), not a boundary: say so loudly rather
    # than let it pass for a violation.
    assert not answer["broken"], f"test environment error: {answer['broken']}"
    return answer["blocked"]


def describe(failures: dict[str, str]) -> str:
    return "; ".join(f"{module} imports {why}" for module, why in failures.items())


def test_the_walk_finds_the_layers():
    """The walk reaches each layer, so an empty one cannot pass for clean."""
    assert "kubed.selenium_flow.flows.engine" in NO_PROTOCOL
    assert "kubed.selenium_flow.session.store" in NO_PROTOCOL
    assert "kubed.selenium_flow.site_data.snapshot" in NO_PROTOCOL
    assert "kubed.selenium_flow.core.coerce" in NO_PROTOCOL
    assert "kubed.selenium_flow.flows.api" not in NO_PROTOCOL
    assert len(NO_PROTOCOL) >= 30


def test_the_kernel_imports_no_protocol_library():
    """Nothing outside `mcp/`, `http/` and `flows/api` reaches the protocol.

    `session/sessions` and `secrets` lean on `mcp` and `starlette` only inside
    functions; those stay lazy, which is what this proves.
    """
    failures = import_failures(NO_PROTOCOL, PROTOCOL)
    assert not failures, describe(failures)


def test_the_plain_modules_import_without_selenium():
    """The flow engine, the vocabulary and the snapshot need no driver."""
    modules = [m for m in NO_SELENIUM if m not in SELENIUM_EXCEPTIONS]
    failures = import_failures(modules, (*PROTOCOL, "selenium"))
    assert not failures, describe(failures)


def test_every_selenium_exception_still_fails():
    """An exception that now imports clean is a stale line, so it is removed."""
    failures = import_failures(SELENIUM_EXCEPTIONS, ("selenium",))
    assert set(failures) == set(SELENIUM_EXCEPTIONS), (
        "now selenium-free, delete from SELENIUM_EXCEPTIONS: "
        f"{sorted(set(SELENIUM_EXCEPTIONS) - set(failures))}"
    )


def test_the_boundary_has_teeth():
    """A module that does import selenium is caught, so a pass means something."""
    failures = import_failures(["kubed.selenium_flow.core.browser"], ("selenium",))
    assert "selenium" in failures["kubed.selenium_flow.core.browser"]
