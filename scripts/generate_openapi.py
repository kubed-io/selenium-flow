#!/usr/bin/env python3
"""Regenerate openapi.yaml, the build artifact.

The spec is generated and NOT committed — `.gitignore` holds it out, CI writes
it, lints it with redocly and uploads it. Nothing in a pull request reviews this
file; what is reviewed is the code that produces it, which `test_openapi.py`
holds against the live tools. Run this when you want to read the document, or
when redocly failed in CI and you want the same input it had.

    python scripts/generate_openapi.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kubed.selenium_flow.routes import ENDPOINTS
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.spec import PLACEHOLDER_VERSION, build_spec

OUT = pathlib.Path(__file__).resolve().parent.parent / "openapi.yaml"


async def main() -> None:
    # A token so the security scheme is described; the address is never dialled.
    server = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token="generated")
    spec = await build_spec(server.mcp, ENDPOINTS, "", authenticated=True)
    # info.version is required by the spec, so it cannot simply be dropped —
    # but stamping the real one would churn this file on every commit, since
    # setuptools_scm derives it from git. The artifact is version-agnostic and
    # the served document at /openapi.yaml carries the true version.
    spec["info"]["version"] = PLACEHOLDER_VERSION
    OUT.write_text(yaml.safe_dump(spec, sort_keys=False, width=100))
    print(f"wrote {OUT} ({len(spec['paths'])} paths)")


if __name__ == "__main__":
    asyncio.run(main())
