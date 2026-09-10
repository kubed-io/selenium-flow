"""What ships in the image, and what rebuilds it.

These are the same question asked from two ends, and they drifted apart: the
admin UI and the embedded skill live outside `kubed/` but are mapped into the
package, so they are in the wheel and therefore in the image — while
`image.yml` only rebuilt on `kubed/**`. A UI change built nothing, and the
deployed image silently kept the old one.
"""

import pathlib
import tomllib

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
IMAGE_WORKFLOW = REPO / ".github" / "workflows" / "image.yml"


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
    """If the filters ever go away the rest of this file passes vacuously."""
    filters = image_trigger_paths()
    assert filters, "image.yml has no paths filter — this test proves nothing"
    assert len(filters) == 2, "expected a pull_request and a push filter"


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
