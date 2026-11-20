"""Suggestions for a name a person is filling in.

VS Code asks for completions when a person opens a resource template or picks a
prompt. Flow and file names are suggested from the resources that list them,
read for the caller who asked, so a suggestion is always one that caller could
read.
"""

from __future__ import annotations

import json
import logging

from kubed.mcp.protocol import PromptReference, ResourceTemplateReference

from ..faults import Traceback

log = logging.getLogger(__name__)

# What each name is completed from: the listing resource, the key of its list,
# and the key of a name inside one entry.
FLOW_NAMES = ("flow://flows", "flows", "name")
FILE_NAMES = ("workspace://files", "files", "name")
SCREENSHOT_NAMES = ("workspace://files/screenshots", "files", "name")
DOWNLOAD_NAMES = ("session://downloads", "files", "name")

# Which (reference, argument) pairs ask for which names.
TEMPLATES = {
    ("flow://flows/{name}", "name"): FLOW_NAMES,
    ("workspace://files/{name}", "name"): FILE_NAMES,
    ("workspace://files/screenshots/{name}", "name"): SCREENSHOT_NAMES,
    ("session://downloads/{name}", "name"): DOWNLOAD_NAMES,
}
PROMPTS = {("repair_flow", "flow"): FLOW_NAMES}


async def _names(mcp, source) -> list[str]:
    uri, collection, key = source
    try:
        result = await mcp.read_resource(uri)
        listing = json.loads(result.contents[0].content)
    except Exception as exc:  # noqa: BLE001 - no names is still an answer
        log.debug("no completions from %s:\n%s", uri, Traceback(exc))
        return []
    return [str(entry[key]) for entry in listing.get(collection, []) if key in entry]


def register(mcp) -> None:
    """Answer `completion/complete` for flow and file names.

    Through FastMCP's own server, `mcp.server`: QuickMCP passes neither its
    completion handler nor its `read_resource` through.
    """
    server = mcp.server

    @server.completion
    async def complete(reference, argument, context):
        if isinstance(reference, ResourceTemplateReference):
            source = TEMPLATES.get((reference.uri, argument.name))
        elif isinstance(reference, PromptReference):
            source = PROMPTS.get((reference.name, argument.name))
        else:
            source = None
        if source is None:
            return None
        typed = (argument.value or "").lower()
        return [name for name in await _names(server, source) if typed in name.lower()]
