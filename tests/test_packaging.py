"""What ships in the image, what rebuilds it, and which build wins.

These are the same question asked from two ends, and they drifted apart: the
admin UI and the embedded skill live outside `kubed/` but are mapped into the
package, so they are in the wheel and therefore in the image — while
`image.yml` only rebuilt on `kubed/**`. A UI change built nothing, and the
deployed image silently kept the old one.
"""

import ast
import pathlib
import sys

# tomllib is 3.11+. The package supports 3.10, so on that leg the reader is
# tomli — the same parser tomllib was adopted from, pulled in by the `test`
# extra under the same marker. Without this the whole module fails to import and
# every test in it is skipped as a collection error, which is how it went
# unnoticed until the matrix started sweeping 3.10.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 only
    import tomli as tomllib

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
IMAGE_WORKFLOW = REPO / ".github" / "workflows" / "image.yml"
DOCKERFILE = REPO / "Dockerfile"

sys.path.insert(0, str(REPO / "scripts"))
import requirements  # noqa: E402 - needs the path above


def packaged_directories() -> set[str]:
    """Every source directory `package-dir` maps into the wheel."""
    data = tomllib.loads(PYPROJECT.read_text())
    mapping = data["tool"]["setuptools"]["package-dir"]
    return {source for source in mapping.values() if source}


def image_trigger_paths() -> list[set[str]]:
    """The `paths:` filter of each trigger in image.yml that has one."""
    # `on` is parsed as the boolean True by YAML 1.1, which is what PyYAML
    # implements — so it cannot be looked up by the string "on".
    spec = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    triggers = spec.get("on", spec.get(True))
    return [set(t["paths"]) for t in triggers.values() if isinstance(t, dict) and "paths" in t]


def test_the_workflow_has_the_path_filters_this_is_about():
    """If the filters ever go away the rest of this file passes vacuously.

    There is exactly one filter, on `push: main`. The `pull_request` trigger was
    removed deliberately — a ~9 minute multi-arch build is most of a pull
    request's wait for a signal that almost never differs from the merge build,
    which is the one whose output anyone actually pulls.
    """
    filters = image_trigger_paths()
    assert filters, "image.yml has no paths filter — this test proves nothing"
    assert len(filters) == 1, "expected exactly the push filter"


@pytest.mark.parametrize("directory", sorted(packaged_directories()))
def test_everything_in_the_wheel_rebuilds_the_image(directory):
    """A directory in the wheel is in the image, so changing it must rebuild.

    Otherwise the change is committed, CI is green, nothing builds, and the
    running container keeps serving the previous version of that file with no
    signal anywhere that it is stale.
    """
    for paths in image_trigger_paths():
        assert f"{directory}/**" in paths, (
            f"{directory}/ is mapped into the package by package-dir, so it "
            f"ships in the image — but image.yml will not rebuild when it "
            f"changes. Add '{directory}/**' to every paths filter in it."
        )


def test_the_package_source_directories_exist():
    """A mapping to a directory that is gone is a wheel missing a feature."""
    for directory in packaged_directories():
        assert (REPO / directory).is_dir(), f"package-dir maps missing {directory}/"


def declared_packages() -> set[str]:
    """Every package name pyproject says ships."""
    data = tomllib.loads(PYPROJECT.read_text())
    return set(data["tool"]["setuptools"]["packages"])


def source_packages() -> set[str]:
    """Every importable package in the source tree, as a dotted name."""
    return {
        ".".join(init.relative_to(REPO).parent.parts)
        for init in (REPO / "kubed").rglob("__init__.py")
    }


def test_every_source_package_is_declared():
    """`packages` is an explicit list, so a package missing from it is simply
    absent from the wheel — no error at build, none at import, and none until
    something in an installed copy reaches for it.

    Which is the whole failure: CI is green, the image builds, and the feature
    is gone. Nothing asserted this before the package had subpackages to lose.
    """
    missing = source_packages() - declared_packages()
    assert not missing, (
        "add to [tool.setuptools] packages in pyproject.toml: " f"{sorted(missing)}"
    )


# --- which build wins -------------------------------------------------------


def image_concurrency() -> dict:
    spec = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    return spec["concurrency"]


def test_a_superseded_branch_build_is_cancelled():
    """A build takes ~9 minutes. Queueing them meant waiting out an image for a
    commit that had already been superseded before the one you wanted started.
    """
    assert image_concurrency()["cancel-in-progress"] == "${{ !inputs.tag }}"


def test_a_release_build_can_never_be_cancelled_by_a_push():
    """publish.yml cuts the version tag BEFORE calling this workflow.

    So a release build cancelled by an ordinary push to main strands a git tag
    and a GitHub Release pointing at an image that was never published — the
    exact failure the "run publish with push=false first" rule exists to avoid.
    Keying the group on the tag gives each release a group of its own, and the
    cancel flag is off for anything carrying one.
    """
    concurrency = image_concurrency()
    assert "inputs.tag" in concurrency["group"], (
        "a release build must not share a concurrency group with branch builds"
    )
    assert concurrency["cancel-in-progress"] == "${{ !inputs.tag }}", (
        "cancel-in-progress must be off whenever inputs.tag is set"
    )


# ---- the requirement list the image installs ---------------------------------
#
# The Dockerfile installs dependencies in a stage that sees pyproject.toml and
# the script that reads it, and nothing else — that is what keeps a source edit
# from invalidating the install. These hold the script to pyproject.toml, and
# the Dockerfile to the split.


def test_the_runtime_list_is_what_pyproject_declares():
    """Read, never restated. A second copy of this list is a second thing to
    keep in step, and the way it fails is an image built against dependencies
    nobody declared."""
    data = tomllib.loads(PYPROJECT.read_text())
    assert requirements.runtime(data, []) == data["project"]["dependencies"]


def test_an_extra_is_appended_whole():
    """`[redis]` is baked into the image so that turning on shared saved
    sessions is a config change rather than a different build."""
    data = tomllib.loads(PYPROJECT.read_text())
    extra = data["project"]["optional-dependencies"]["redis"]
    assert requirements.runtime(data, ["redis"])[-len(extra):] == extra


def test_an_extra_that_does_not_exist_says_which_do():
    """It runs inside a Docker layer, where a KeyError is a traceback with no
    context and the fix is in a file the reader is not looking at."""
    data = tomllib.loads(PYPROJECT.read_text())
    with pytest.raises(SystemExit) as raised:
        requirements.runtime(data, ["nope"])
    assert "no [nope] extra" in str(raised.value)
    assert "redis" in str(raised.value), "it has to name what there is"


def test_the_build_list_is_the_pep_518_one():
    """`[build-system].requires` already names and pins the backend — it is the
    one list that has to be right for `pip install .` to work anywhere."""
    data = tomllib.loads(PYPROJECT.read_text())
    assert requirements.build(data, []) == data["build-system"]["requires"]


def dockerfile_stages() -> dict[str, list[str]]:
    """Each `FROM ... AS <name>` stage, as its list of instruction lines.

    Parsed rather than split on the word FROM, which also appears in the prose
    at the top of the file — a split found the comment and silently returned an
    empty stage, so a test passed by testing nothing.
    """
    stages: dict[str, list[str]] = {}
    current = None
    for raw in DOCKERFILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("FROM ") and " AS " in line:
            current = line.rsplit(" AS ", 1)[1]
            stages[current] = []
        elif current is not None and line and not line.startswith("#"):
            stages[current].append(line)
    return stages


def test_the_dependencies_are_installed_before_the_source():
    """The whole point of the ordering. `COPY . .` brings .git with it, because
    setuptools_scm needs it — so putting it in front of the install meant every
    commit reinstalled selenium, docs-only ones included."""
    builder = dockerfile_stages()["builder"]
    deps_copy = builder.index("COPY pyproject.toml ./")
    install = next(i for i, ln in enumerate(builder) if "requirements.txt" in ln)
    source_copy = builder.index("COPY . .")
    assert deps_copy < install < source_copy


def test_the_runner_receives_a_venv_and_installs_nothing():
    """A venv is one directory holding the libraries and the console script, so
    `COPY --from` moves the whole installed program in one instruction. That is
    what lets the builder be fat and the runner be slim WITHOUT the runner
    resolving and downloading every dependency a second time — which is what it
    used to do, and what made the build twice as long."""
    runner = dockerfile_stages()["runner"]
    assert "COPY --from=builder /opt/venv /opt/venv" in runner
    assert not [ln for ln in runner if ln.startswith("pip install")]
    assert "COPY . ." not in runner, "the source has no business in the runner"


def test_the_build_tooling_never_reaches_the_runner():
    """git is in the builder because setuptools_scm reads the version from it,
    and it has no business in a running pod. Nothing is FROM the builder, so it
    cannot leak — and the fat image already HAS git, which is why there is no
    apt-get to audit in the first place."""
    stages = dockerfile_stages()
    instructions = [ln for lines in stages.values() for ln in lines]
    assert not [ln for ln in instructions if "apt-get" in ln], (
        "python:X ships git; only -slim needed an apt-get to put it back"
    )
    body = DOCKERFILE.read_text()
    assert "FROM python:${PY_VERSION} AS builder" in body
    assert "FROM python:${PY_VERSION}-slim AS runner" in body
    assert "FROM builder" not in body, "nothing inherits the build tooling"


def test_the_copied_venv_is_proved_to_work_at_build_time():
    """Copying a venv across image variants assumes the interpreter is at the
    same path and every wheel is self-contained. True here, and worth failing
    the BUILD over rather than a pod: the import pulls the whole dependency
    tree."""
    runner = dockerfile_stages()["runner"]
    assert any("kubed.selenium_flow.server" in ln for ln in runner)


def test_the_project_is_installed_with_its_extra():
    """`[redis]` is what lets shared saved sessions be an env var rather than a
    different image. `--no-deps` would silently drop it."""
    builder = dockerfile_stages()["builder"]
    install = next(ln for ln in builder if ln.startswith("pip install --no-cache-dir ."))
    assert "[redis]" in install
    assert "--no-deps" not in install


def test_the_toml_reader_works_on_the_oldest_python_the_image_can_build():
    """PY_VERSION is an ARG and the project's requires-python is >=3.10, but
    tomllib is 3.11+. Reading pyproject.toml with a 3.11-only parser quietly
    made `PY_VERSION=3.10 docker compose build` impossible. The backport
    tomllib was adopted from covers it, under the same marker pyproject.toml's
    own [test] extra already uses — and it has to be installed BEFORE the
    reader runs, which is the part an ordering change would break silently."""
    source = (REPO / "scripts" / "requirements.py").read_text()
    assert "import tomli as tomllib" in source
    builder = dockerfile_stages()["builder"]
    backport = next(i for i, ln in enumerate(builder) if "tomli" in ln)
    reader = next(i for i, ln in enumerate(builder) if "requirements.py runtime" in ln)
    assert backport < reader, "the parser must exist before the script runs"
    assert "python_version < '3.11'" in builder[backport]


def test_everything_the_dockerfile_copies_by_name_rebuilds_the_image():
    """The same failure as the wheel/trigger pairing above, by another route.

    `scripts/requirements.py` is not in the wheel, so the packaged-directory
    rule never covered it — but the Dockerfile copies it and it decides which
    dependencies get installed. A change to it would have changed the image
    while triggering no build, leaving the deployed image on the old
    dependency-selection logic.

    Derived from the Dockerfile's own COPY lines rather than from a list
    someone has to remember to extend. `COPY . .` is excluded: it is the whole
    context, and what matters in it is the wheel, which the test above covers.
    """
    named = []
    for lines in dockerfile_stages().values():
        for line in lines:
            if not line.startswith("COPY ") or "--from=" in line:
                continue
            source = line.split()[1]
            if source != ".":
                named.append(source)
    assert named, "no named COPY found — has the Dockerfile changed shape?"

    watched = {path for paths in image_trigger_paths() for path in paths}
    for source in named:
        covered = source in watched or any(
            pattern.endswith("/**") and source.startswith(pattern[:-2])
            for pattern in watched
        )
        assert covered, f"{source} is copied into the image but triggers no build"


def test_pip_does_not_ship_inside_the_copied_venv():
    """`python -m venv` seeds pip, and /opt/venv is copied into the runner
    WHOLE — so pip ships unless it is removed, at whatever version was latest
    on the day. .hadolint.yaml waives the pin-your-pip rule on the grounds that
    pip never reaches the image, so this line is what makes that waiver true.
    """
    builder = dockerfile_stages()["builder"]
    assert "pip uninstall --yes pip" in builder
    # Last, or the steps after it have no pip to run with.
    removal = builder.index("pip uninstall --yes pip")
    assert not [ln for ln in builder[removal + 1:] if ln.startswith("pip ")]


# --- what a wheel has to contain, beyond the modules ------------------------

# Each module that resolves a sibling data directory, and the directory it
# reads. The packaged lookup is `Path(__file__).parent / <dir>`, so a wheel has
# to place that directory BESIDE the module — which is what package-dir decides.
DATA_DIRS = {
    "kubed/selenium_flow/core/js.py": "js",
    "kubed/selenium_flow/mcp/skill.py": "skills",
    "kubed/selenium_flow/mcp/prompts.py": "prompts",
    "kubed/selenium_flow/http/admin.py": "static",
}


@pytest.mark.parametrize(("module", "directory"), sorted(DATA_DIRS.items()))
def test_a_data_directory_is_packaged_beside_the_module_that_reads_it(module, directory):
    """Move the module, move the mapping — or the installed server has no files.

    This is invisible to every other test. In a source checkout the fallback
    lands on the repo root and everything works; in a wheel the packaged path is
    the only one, and the fallback resolves to site-packages. So the admin UI
    404s its own page, or the server serves no skill, with nothing failing until
    somebody installs it (Copilot, #36).
    """
    package = ".".join(pathlib.Path(module).parent.parts)
    key = f"{package}.{directory}"
    data = tomllib.loads(PYPROJECT.read_text())
    mapping = data["tool"]["setuptools"]["package-dir"]
    assert (REPO / module).is_file(), f"{module} moved — update DATA_DIRS"
    assert key in mapping, (
        f"{module} reads ./{directory} beside itself, so package-dir needs "
        f"'{key}' — otherwise the wheel puts it somewhere the module cannot look"
    )
    assert mapping[key] == directory
    assert key in set(data["tool"]["setuptools"]["packages"])
    # package-dir says WHERE it goes; package-data says whether anything inside
    # it ships at all. Without a pattern the wheel carries an empty directory
    # and the module finds nothing in it (Copilot, #36).
    patterns = data["tool"]["setuptools"]["package-data"].get(key)
    assert patterns, f"[tool.setuptools.package-data] needs a pattern for '{key}'"


def test_every_relative_import_in_the_package_resolves():
    """A relative import inside a function is not checked until it runs.

    `session/sessions.py` carried `from .core import browser` after the move —
    which names `session.core`, a package that does not exist. It sat in the
    live-session branch, where the tests mock the reconnect, so the status
    quietly stopped reporting in_frame and the real window size instead of
    failing (Copilot, #36).
    """
    root = REPO / "kubed" / "selenium_flow"
    broken = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            base = path.parent
            for _ in range(node.level - 1):
                base = base.parent
            dots = "." * node.level
            # `from . import x` has module=None and names its targets in the
            # aliases. Skipping that form skipped every sibling import in the
            # new subpackages — the guard passed while naming nothing (Copilot).
            if node.module:
                heads = [(node.module.split(".")[0], f"{dots}{node.module}")]
            else:
                heads = [(a.name, f"{dots}{a.name}") for a in node.names]
            for head, shown in heads:
                if (base / head).is_dir() or (base / f"{head}.py").is_file():
                    continue
                # A `from . import name` may also import a symbol from the
                # package __init__ rather than a module; only flag it when the
                # package does not define it either.
                init = base / "__init__.py"
                if not node.module and init.is_file() and head in init.read_text():
                    continue
                broken.append(f"{path.relative_to(REPO)}:{node.lineno} {shown}")
    assert not broken, "relative imports that name nothing: " + "; ".join(broken)
