"""The README is also the Docker Hub description, which has a hard limit.

Docker Hub truncates a repository description at 25,000 bytes. The publish
workflow pushes README.md as that description, so exceeding the limit does not
fail anything — it silently ships a manual that stops mid-sentence, and says so
only in a build warning nobody reads.

This is here because it happened: the README passed 25,000 bytes while tools
were being added, and the truncation was noticed in a workflow log rather than
by anyone reading the page.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

# Docker Hub's documented ceiling for a repository description.
DOCKER_HUB_LIMIT = 25_000
README = pathlib.Path(__file__).parent.parent / "README.md"


def test_the_readme_fits_docker_hubs_description_limit():
    size = len(README.read_bytes())
    assert size <= DOCKER_HUB_LIMIT, (
        f"README.md is {size} bytes, over Docker Hub's {DOCKER_HUB_LIMIT}-byte "
        f"limit by {size - DOCKER_HUB_LIMIT}. It would be published truncated. "
        "Move contributor material to CONTRIBUTING.md and design rationale to "
        "AGENTS.md — the README advertises, those two explain."
    )
