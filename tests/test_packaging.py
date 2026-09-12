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
    empty stage, so the test below passed by testing nothing.
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


def test_the_dependency_stage_cannot_see_the_source():
    """The whole point of the split. A `COPY . .` here would put the install
    back behind every source edit — which is where it was, and why the build
    was more than twice as long."""
    copies = [ln for ln in dockerfile_stages()["deps"] if ln.startswith("COPY ")]
    assert copies == [
        "COPY pyproject.toml ./",
        "COPY scripts/requirements.py ./scripts/",
    ]


def test_nothing_the_wheel_is_built_with_reaches_the_runner():
    """`runner` is FROM deps, so anything installed in `deps` ships. git is
    there to let setuptools_scm read the version and has no business in a
    running pod, so it goes in `wheel` — which nothing is FROM."""
    stages = dockerfile_stages()
    assert "git" not in " ".join(stages["deps"]), "deps ships; keep git out of it"
    assert "apt-get install -y --no-install-recommends git" in stages["wheel"]
    # And the shape that makes that true: the runner inherits the dependency
    # layer, not the stage that built the wheel.
    body = DOCKERFILE.read_text()
    assert "FROM deps AS runner" in body
    assert "FROM deps AS wheel" in body


def test_the_runner_installs_the_wheel_the_ordinary_way():
    """Not `--no-deps`. Everything is already in the layer underneath, so pip
    reports each requirement satisfied and installs only our wheel — and if the
    two ever drift it fixes that rather than shipping an ImportError. `--no-deps`
    would also silently ignore the [redis] extra, which is the whole reason the
    image can turn on shared sessions with an env var."""
    runner = dockerfile_stages()["runner"]
    install = next(ln for ln in runner if ln.startswith("pip install"))
    assert "--no-deps" not in install
    assert "[redis]" in install
