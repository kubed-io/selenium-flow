"""The kernel imports no protocol library and, below it, no driver.

`AGENTS.md` shapes the package in layers: the protocol (`mcp/`, `http/`) sits on
top, Selenium on the bottom (`core/browser`, `core/actions`, `core/pointer`,
`core/probe`), and what lies between -- the flow engine, the workspace store, the
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

A third boundary is read off the source rather than imported: the kernel never
imports *upward*, into this package's own protocol layers (`mcp`, `http`,
`routes`, `server`, `spec`). Blocking a library cannot see that — a module of
ours with no imports passes the first boundary from anywhere — so the files are
walked, lazy imports included, and every offending import is named.
"""

import ast
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
    *walk("workspace"),
    *walk("flows", skip=("api",)),
    *walk("site_data"),
    *walk("recordings"),
    *walk("monitor"),
    *(
        f"kubed.selenium_flow.{name}"
        for name in (
            "names", "urls", "errors", "faults", "binding", "secrets", "config"
        )
    ),
)

# Layer 2: these also import with selenium blocked.
NO_SELENIUM = tuple(
    f"kubed.selenium_flow.{name}"
    for name in (
        "core.defaults",
        "core.naming",
        "core.coerce",
        "core.assertion",
        "workspace.store",
        "site_data.snapshot",
        "recordings.mp4",
        "monitor.events",
        "flows.template",
        "flows.redact",
        "flows.engine",
        "flows.report",
        "names",
        "urls",
        "faults",
        "binding",
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
    # Classifies the Selenium exception classes: which are transient, which
    # are the caller's fault and which mean the session is gone. The plain
    # exceptions and `message`/`formatted` are in `faults`, which is clean.
    "kubed.selenium_flow.errors": "classification tuples are selenium exceptions",
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
    assert "kubed.selenium_flow.monitor.events" in NO_PROTOCOL
    assert "kubed.selenium_flow.workspace.store" in NO_PROTOCOL
    assert "kubed.selenium_flow.site_data.snapshot" in NO_PROTOCOL
    assert "kubed.selenium_flow.core.coerce" in NO_PROTOCOL
    assert "kubed.selenium_flow.flows.api" not in NO_PROTOCOL
    assert len(NO_PROTOCOL) >= 30


def test_the_kernel_imports_no_protocol_library():
    """Nothing outside `mcp/`, `http/` and `flows/api` reaches the protocol.

    `workspace/workspaces` and `secrets` lean on `mcp` and `starlette` only inside
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


# ---- up from down -----------------------------------------------------------

PACKAGE_NAME = "kubed.selenium_flow"

# This package's own protocol layers. Nothing below them imports from them.
UPPER = tuple(
    f"{PACKAGE_NAME}.{name}" for name in ("mcp", "http", "routes", "server", "spec")
)

# The kernel: the same layers `NO_PROTOCOL` walks, `flows/api` excepted.
KERNEL = ("core", "workspace", "flows", "site_data", "recordings", "monitor")
KERNEL_SKIP = {"flows/api.py"}

# Upward imports that exist today, each with the reason. Keyed by file and the
# module it reaches; `test_every_upward_exception_still_exists` fails once one
# is gone, so the entry has to go with the fix.
UPWARD_EXCEPTIONS: dict[tuple[str, str], str] = {}


def _upward(name: str) -> bool:
    return any(name == up or name.startswith(f"{up}.") for up in UPPER)


def _reached(module: str, node: ast.ImportFrom) -> list[str]:
    """The upward modules a ``from ... import`` in ``module`` reaches, named as
    precisely as the statement allows."""
    if node.level:
        base = module.split(".")[: -node.level]
        target = ".".join([*base, node.module] if node.module else base)
    else:
        target = node.module or ""
    names = [f"{target}.{alias.name}" for alias in node.names]
    if target in UPPER:
        # `from ..mcp import guidance`: the module is the name imported.
        return names
    if _upward(target):
        # `from ..mcp.annotations import hints`: the module is the source.
        return [target]
    # `from .. import routes` reaches a layer through its parent.
    return [name for name in names if _upward(name)]


def upward_imports(relative: str, source: str) -> list[tuple[str, str]]:
    """Every (line, module) in ``source`` that reaches a protocol layer."""
    module = f"{PACKAGE_NAME}." + relative.removesuffix(".py").replace("/", ".")
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            reached = _reached(module, node)
        elif isinstance(node, ast.Import):
            reached = [alias.name for alias in node.names if _upward(alias.name)]
        else:
            continue
        found.extend((f"{relative}:{node.lineno}", name) for name in reached)
    return found


def kernel_imports_upward() -> dict[tuple[str, str], str]:
    """(file, module) -> where, for every upward import in the kernel."""
    found = {}
    for layer in KERNEL:
        for path in sorted((PACKAGE / layer).rglob("*.py")):
            relative = path.relative_to(PACKAGE).as_posix()
            if relative in KERNEL_SKIP:
                continue
            for where, name in upward_imports(relative, path.read_text()):
                found.setdefault((relative, name), where)
    return found


def test_the_kernel_never_imports_upward():
    """`core`, `workspace`, `flows` and `site_data` import nothing from `mcp`,
    `http`, `routes`, `server` or `spec` — at module scope or inside a function."""
    found = kernel_imports_upward()
    offending = {key: where for key, where in found.items()
                 if key not in UPWARD_EXCEPTIONS}
    assert not offending, "; ".join(
        f"{where} imports {name}" for (_, name), where in sorted(offending.items())
    )


def test_every_upward_exception_still_exists():
    """An exception whose import is gone is a stale line, so it is removed."""
    stale = set(UPWARD_EXCEPTIONS) - set(kernel_imports_upward())
    assert not stale, f"no longer imported, delete from UPWARD_EXCEPTIONS: {stale}"


@pytest.mark.parametrize(
    "source",
    [
        "from ..mcp.annotations import hints",
        "from .. import routes",
        "from ..spec import build_spec",
        "def lazy():\n    from ..http import answer",
        "import kubed.selenium_flow.server",
    ],
)
def test_the_upward_check_has_teeth(source):
    """Every spelling of reaching up is caught, so a pass means something."""
    assert upward_imports("core/example.py", source)


def test_the_upward_check_leaves_the_kernel_alone():
    """Imports within and across the kernel are not mistaken for reaching up."""
    source = (
        "from .annotations import hints\n"
        "from ..workspace import store\n"
        "from .. import names, urls\n"
        "import json\n"
    )
    assert upward_imports("core/example.py", source) == []
