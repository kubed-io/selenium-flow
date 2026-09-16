"""Suggestions for a name a person is filling in.

VS Code asks a server for completions when a person opens a resource template
or picks a prompt, and shows them in the quick pick (saga §F3.4). Two names are
worth suggesting — a flow's and a file's — and both are already listed by a
resource, so the suggestions are those listings read back for the caller who
asked. Answered from the same place, they cannot suggest a flow the caller
could not read, or disagree with what `flow://flows` would have shown.
"""

from __future__ import annotations

import json
import logging

from mcp.types import PromptReference, ResourceTemplateReference

log = logging.getLogger(__name__)

# What each name is completed from: the listing resource, the key of its list,
# and the key of a name inside one entry.
FLOW_NAMES = ("flow://flows", "flows", "name")
FILE_NAMES = ("session://files", "files", "name")

# Which (reference, argument) pairs ask for which names.
TEMPLATES = {
    ("flow://flows/{name}", "name"): FLOW_NAMES,
    ("session://files/{name}", "name"): FILE_NAMES,
}
PROMPTS = {("repair_flow", "flow"): FLOW_NAMES}


async def _names(mcp, source) -> list[str]:
    uri, collection, key = source
    try:
        result = await mcp.read_resource(uri)
        listing = json.loads(result.contents[0].content)
    except Exception:
        log.debug("no completions from %s", uri, exc_info=True)
        return []
    return [str(entry[key]) for entry in listing.get(collection, []) if key in entry]


def register(mcp) -> None:
    """Answer `completion/complete` for flow and file names."""

    @mcp.completion
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
        return [name for name in await _names(mcp, source) if typed in name.lower()]
