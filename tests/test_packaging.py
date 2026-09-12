"""What ships in the image, what rebuilds it, and which build wins.

These are the same question asked from two ends, and they drifted apart: the
admin UI and the embedded skill live outside `kubed/` but are mapped into the
package, so they are in the wheel and therefore in the image — while
`image.yml` only rebuilt on `kubed/**`. A UI change built nothing, and the
deployed image silently kept the old one.
"""

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
