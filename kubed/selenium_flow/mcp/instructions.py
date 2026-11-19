"""The server ``instructions``, chosen for each connection.

What a client is told depends on whether the skill is served and whether its
model reads resources, so the text is chosen on each handshake: ``initialize``
and ``server/discover`` both carry it.
"""

from __future__ import annotations

from fastmcp.server.middleware import Middleware

from ..workspace.workspaces import NAME_PARAM
from . import clients

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

# Appended only when the skill is served.
SKILL_POINTER = """
How to drive this well — when to screenshot rather than extract, what a timeout \
on a good XPath usually means, how to write a flow — is at \
skill://selenium-flow/SKILL.md. Read it{how} before your first call; it ships \
with this server, so it describes this version of it.
"""

# For a client whose model cannot read resources: it is told how to read a URI.
READING_POINTER = """
Everything this server has to read is a URI — workspace://current, \
session://downloads, flow://flows, secret://secrets and the like, wherever a \
hint or an error names one. Read one with read_resource(uri); list_resources \
shows them all.
"""


def instructions(skill_available: bool = True, reads_resources: bool = True) -> str:
    """What a client reads at connect, for the server it got and what it can read."""
    text = INSTRUCTIONS
    if not reads_resources:
        text += READING_POINTER
    if skill_available:
        how = "" if reads_resources else " with read_resource"
        text += SKILL_POINTER.format(how=how)
    return text


def first_instructions(skill_available: bool = True) -> str:
    """What the server object starts with, before any handshake has named a client.

    The same text `InstructionsFor` gives a client it cannot identify.
    """
    unknown = clients.reads_resources(clients.Caller.stdio(client=""))
    return instructions(skill_available, unknown)


class InstructionsFor(Middleware):
    """Answer each handshake with the instructions for the client it came from.

    A client that cannot read resources is told to use read_resource.
    """

    def __init__(self, skill_available: bool):
        self.skill_available = skill_available

    def _text(self, context) -> str:
        caller = clients.caller(clients.named_in(context.message))
        return instructions(self.skill_available, clients.reads_resources(caller))

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is not None:
            result.instructions = self._text(context)
        return result

    async def on_discover(self, context, call_next):
        result = await call_next(context)
        if isinstance(result, dict):
            result["instructions"] = self._text(context)
        elif result is not None:
            result.instructions = self._text(context)
        return result
