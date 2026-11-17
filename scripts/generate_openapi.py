#!/usr/bin/env python3
"""Regenerate openapi.yaml, the build artifact.

The document is not committed: CI writes it, lints it with redocly and uploads
it, and `test_openapi.py` holds the code that produces it against the live
tools. Run this to read the document, or to give redocly the input CI had.

    python scripts/generate_openapi.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.capabilities import ENDPOINTS
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.spec import PLACEHOLDER_VERSION, build_spec

OUT = pathlib.Path(__file__).resolve().parent.parent / "openapi.yaml"


async def main() -> None:
    # A token so the security scheme is described; the address is never dialled.
    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": "generated"})
    )
    spec = await build_spec(server.mcp, ENDPOINTS, "", authenticated=True)
    # info.version is required, and the real one comes from git through
    # setuptools_scm. The served /openapi.yaml carries it.
    spec["info"]["version"] = PLACEHOLDER_VERSION
    OUT.write_text(yaml.safe_dump(spec, sort_keys=False, width=100))
    print(f"wrote {OUT} ({len(spec['paths'])} paths)")


if __name__ == "__main__":
    asyncio.run(main())
