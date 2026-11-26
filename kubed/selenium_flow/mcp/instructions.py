"""The server ``instructions``, chosen for each connection.

What a client is told depends on whether its model reads resources and on how
it gets the plugin's material, so the text is chosen on each handshake.
kubed-mcp calls `for_delivery` on each ``initialize`` and ``server/discover``,
with the inputs that request resolved (DLV-11); the server is built with
`instructions()`, the text kubed-mcp falls back to.
"""

from __future__ import annotations

from kubed.mcp.delivery import Resolved

from ..core.guidance import ENTRY, ENTRY_SKILL
from ..names import SERVER_NAME
from ..workspace.workspaces import NAME_PARAM

INSTRUCTIONS = f"""\
Drives a real Chrome or Firefox browser on Selenium Grid. The browser is \
persistent: it stays alive between tool calls and keeps its page, cookies and \
scroll position.

Name your workspace first: add ?{NAME_PARAM}=<name> to the MCP URL, or send \
an Sf-Workspace header. Every call is then about that workspace, and no call \
takes a session id — calling again with the same name is how you get the same \
browser back, after a reconnect or a restart.

Lifecycle:
1. Call open_session to start a browser. Pass browser="firefox" for Firefox; \
the default is Chrome. Every other tool behaves identically on both.
2. Call the other tools. They act on your workspace's browser.
3. Call end_browser when finished, including after a failure. Browsers are a \
scarce resource and an abandoned one holds a slot until the Grid reaps it. Your \
workspace survives it, so open_session picks up where you left off.

A workspace can hold more sessions, each a browser of its own. To work in a \
named one, pass its session on every call: a call without it acts in your own. \
Its state is session://<name>/… (console, network, site-data, downloads), and \
a result's see or uri already names it. workspace:// is the connection's own \
session: for a subagent on its main agent's connection, the main agent's.

Elements are addressed by XPath or by CSS - pass one or the other, never \
both: selector={{"xpath": "//input[@name='q']"}} or \
selector={{"css": "input[name=q]"}}.

Most actions take an optional url. It is not an assertion: if the browser is \
somewhere else it navigates there first, so you can jump straight to a page \
instead of clicking a path to it.

Prefer extract to read a page — it is far cheaper than a screenshot. Use \
execute_script for anything the other tools do not cover, scrolling included.

"""

SKILL_POINTER = f"""
How to drive this well — when to screenshot rather than extract, what a timeout \
on a good XPath usually means, how to write a flow — is at \
{ENTRY}. Read it{{how}} before your first \
call; it ships with this server, so it describes this version of it.
"""

# For a client that has the plugin from this server by another way, the hook
# plugins' shim: the skill is held, so no URI is named.
SKILL_HELD = f"""
How to drive this well — when to screenshot rather than extract, what a timeout \
on a good XPath usually means, how to write a flow — is the {ENTRY_SKILL} \
skill, which your client already has from this server. Read it before your \
first call; it describes this version of the server.
"""

# For a client with the plugin installed, which may lag the server: no version
# claim is made.
SKILL_INSTALLED = f"""
How to drive this well — when to screenshot rather than extract, what a timeout \
on a good XPath usually means, how to write a flow — is the \
{SERVER_NAME}:{ENTRY_SKILL} skill, installed with this server's plugin. Read it \
before your first call.
"""

# For a client whose model cannot read resources: it is told how to read a URI.
READING_POINTER = """
Everything this server has to read is a URI — workspace://current, \
session://downloads, flow://flows, secret://secrets and the like, wherever a \
hint or an error names one. Read one with read_resource(uri); list_resources \
shows where to start.
"""


def instructions(reads_resources: bool = True, via: str = "mcp") -> str:
    """What a client reads at connect, for what it can read and how it gets the
    plugin (DLV-9)."""
    text = INSTRUCTIONS
    if not reads_resources:
        text += READING_POINTER
    if via == "extension":
        return text + SKILL_HELD
    if via == "local":
        return text + SKILL_INSTALLED
    how = "" if reads_resources else " with read_resource"
    return text + SKILL_POINTER.format(how=how)


def for_delivery(delivery: Resolved) -> str:
    """The instructions for a request, by whether it resolved resources off and
    by its Via."""
    return instructions(delivery.resources != "off", via=delivery.via)
