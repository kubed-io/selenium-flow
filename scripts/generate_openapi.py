#!/usr/bin/env python3
"""Regenerate the committed openapi.yaml.

The spec is generated, but checked in so it can be reviewed in a pull request
and linted like any other artifact. `test_openapi.py` fails when the file drifts
from what the code produces, so this script is the fix for that failure.

    python scripts/generate_openapi.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kubed.selenium_flow.openapi import PLACEHOLDER_VERSION, build_spec
from kubed.selenium_flow.routes import ENDPOINTS
from kubed.selenium_flow.server import SeleniumMCP

OUT = pathlib.Path(__file__).resolve().parent.parent / "openapi.yaml"


async def main() -> None:
    # A token so the security scheme is described; the address is never dialled.
    server = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token="generated")
    spec = await build_spec(server.mcp, ENDPOINTS, "/browser", authenticated=True)
    # info.version is required by the spec, so it cannot simply be dropped —
    # but stamping the real one would churn this file on every commit, since
    # setuptools_scm derives it from git. The artifact is version-agnostic and
    # the served document at /openapi.yaml carries the true version.
    spec["info"]["version"] = PLACEHOLDER_VERSION
    OUT.write_text(yaml.safe_dump(spec, sort_keys=False, width=100))
    print(f"wrote {OUT} ({len(spec['paths'])} paths)")


if __name__ == "__main__":
    asyncio.run(main())
