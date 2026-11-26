"""The timing an MCP tool that takes no turn is given: its call is one
`Calls.timed` call, a ``call <tool>`` span and a ``call.finished``, on the
``mcp`` surface (RUN-32).

A tool that acts on a browser is timed by its host (`Calls.begin` and
`Calls.end`), and `open_session` and `end_browser` by the workspace manager.
Every other tool is wrapped here, so none goes untimed.
"""

from __future__ import annotations

import functools
import inspect

import anyio

from . import clients


def timed(workspaces, tool: str):
    """Run a tool declaration, sync or async, inside ``workspaces.calls.timed``
    under the name ``tool``.

    The declaration's signature and docstring are kept (`functools.wraps`), so
    its schema and its description are unchanged. The call names the caller's
    workspace, or for a tool that takes ``session`` the session it reads
    (`Workspaces.reading`, which never writes, never `session_of`, which
    claims); a session that cannot be read is the workspace's, and the tool
    says why itself.
    """

    def wrap(declaration):
        signature = inspect.signature(declaration)
        reads = "session" in signature.parameters

        def named(args, kwargs) -> str | None:
            try:
                given = signature.bind_partial(*args, **kwargs)
            except TypeError:  # the call itself says what is wrong
                return None
            return given.arguments.get("session")

        if inspect.iscoroutinefunction(declaration):

            @functools.wraps(declaration)
            async def run(*args, **kwargs):
                caller, key = _caller()
                if caller is not None and reads:
                    # A read of the store, which may be a network call: off the loop.
                    key = await anyio.to_thread.run_sync(
                        _read, workspaces, caller, named(args, kwargs), key
                    )
                with workspaces.calls.timed(key, tool=tool, surface="mcp"):
                    return await declaration(*args, **kwargs)

            return run

        @functools.wraps(declaration)
        def call(*args, **kwargs):
            caller, key = _caller()
            if caller is not None and reads:
                key = _read(workspaces, caller, named(args, kwargs), key)
            with workspaces.calls.timed(key, tool=tool, surface="mcp"):
                return declaration(*args, **kwargs)

        return call

    return wrap


def _caller():
    """The caller and the workspace it names, or ``(None, "")`` for one that
    names none: the tool refuses it itself."""
    try:
        caller = clients.caller()
        return caller, caller.name
    except Exception:  # noqa: BLE001 - the tool says why
        return None, ""


def _read(workspaces, caller, session: str | None, workspace: str) -> str:
    """The key of the session ``caller`` reads, or ``workspace`` when it cannot
    be read: the tool says why."""
    try:
        return workspaces.reading(caller, session)
    except Exception:  # noqa: BLE001 - the tool says why
        return workspace
