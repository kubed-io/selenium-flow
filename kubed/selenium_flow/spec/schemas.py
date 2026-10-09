"""The response shapes and hand-written schemas the spec assembles.

Data, not logic. Request schemas come from the MCP tools themselves — see
``builder.py`` — so what lives here is the half with nothing to introspect: the
ops endpoints' answers, and the ``/flows`` and ``/files`` operations, which are
paths carrying documents rather than an action's arguments. What each browser
action returns is a column of its row in ``core/capabilities.py``, and
``RESPONSES`` reads it from there.

Split from the builder because fifteen literal blobs beside fourteen functions
made one file nobody could hold in their head — and because a schema is read,
while a builder is followed.
"""

from __future__ import annotations

from ..core.capabilities import CAPABILITIES, SITE_DATA_HINT

# What each capability answers is a column of its row in `core.capabilities`,
# so a row cannot be written without one. This is that column by name, plus
# the one answer that belongs to no capability: the workspace status.
RESPONSES = {
    **{row.name: row.response for row in CAPABILITIES},
    # What `GET /browser` and `workspace://current` answer with. Hand-written like
    # every capability's answer, and it was missing — so the published status
    # operation was an empty object and a generated client could not read it
    # (Copilot, #34).
    "current_workspace": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "The name you called with."},
            "named_by": {
                "type": "string",
                "enum": ["header", "query", "stdio", "request"],
                "description": "Which mechanism supplied the name.",
            },
            "principal": {
                "type": ["object", "null"],
                "description": (
                    "Who the credential says you are: kind admin or oidc, with"
                    " subject and username for oidc. Null on an open server."
                ),
                "properties": {
                    "kind": {"type": "string", "enum": ["admin", "oidc"]},
                    "subject": {"type": ["string", "null"]},
                    "username": {"type": ["string", "null"]},
                },
            },
            "browser": {"type": ["string", "null"]},
            "url": {"type": ["string", "null"], "description": "The page it is on."},
            "live": {
                "type": "boolean",
                "description": "Whether a session is open in this workspace.",
            },
            "recording": {
                "type": "boolean",
                "description": (
                    "Whether the browser this workspace holds is being recorded."
                ),
            },
            "in_frame": {"type": ["boolean", "null"]},
            "window": {
                "type": ["string", "null"],
                "description": "Window size as WxH, when one is known.",
            },
            "store": {"type": "string", "enum": ["memory", "redis"]},
            "settings": {"type": "object"},
            "site_data": {
                "type": "object",
                "description": (
                    "Present only when the workspace has saved site data."
                ),
                "properties": {
                    "sites": {
                        "type": "integer",
                        "description": "How many sites have saved data.",
                    },
                    "uri": {
                        "type": "string",
                        "description": "Where to read what is saved.",
                    },
                },
            },
            "guidance": {
                "type": "string",
                "description": "The skill reference that explains sessions.",
            },
        },
    },
}

ERROR = {
    "type": "object",
    "properties": {"error": {"type": "string"}},
    "required": ["error"],
}

HEALTH = {
    "type": "object",
    "properties": {"status": {"type": "string", "const": "ok"}},
}

STARTED = {
    "type": "object",
    "properties": {"status": {"type": "string", "const": "started"}},
}

READY = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "degraded"]},
        "grid": {
            "type": "string",
            "description": "Grid hub this server dials, without any credentials.",
        },
        "grid_ready": {"type": "boolean"},
        "browsers": {"type": "integer", "description": "Browsers held Grid-wide."},
        "workspaces": {
            "type": "string",
            "enum": ["memory", "redis"],
            "description": "Where workspace records are kept.",
        },
        "error": {"type": "string", "description": "Only present when degraded."},
    },
}

INFO = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        "mount": {
            "type": "string",
            "description": "Where this server is mounted — `/` at the root.",
        },
        "mcp": {"type": "string", "description": "Path of the MCP endpoint."},
        "grid": {"type": "string"},
        "workspaces": {"type": "string", "enum": ["memory", "redis"]},
    },
}

WORKSPACE_PARAMETERS = [
    {
        "name": "X-Workspace",
        "in": "header",
        "required": False,
        "schema": {"type": "string"},
        "description": (
            "Which workspace this call is about. What an admin pins inside a "
            "credential when one credential should mean one workspace. Sending "
            "this AND ?workspace= is a 400: two names is two ideas about who is "
            "calling. Its old name, X-Session-Key, is refused with a 400."
        ),
    },
    {
        "name": "workspace",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": (
            "The same thing on the URL, for a caller that cannot set a header. "
            "Anything touching a browser needs one of the two; the flow library "
            "falls back to the shared 'global' one, which is read-only. Its old "
            "name, ?session=, is refused with a 400."
        ),
    },
]


FLOW_STEP = {
    "type": "object",
    "required": ["tool"],
    "description": "One tool call. See GET /flows/schema for what each tool takes.",
    # A real step, so anything generating an example from this document produces
    # something that would actually run. Sampling the properties instead yields
    # `{"tool": "…"}`, which is the right shape and names no tool that exists.
    "example": {"tool": "navigate", "args": {"url": "https://example.com"}},
    "properties": {
        "tool": {"type": "string", "description": "Which action this step runs."},
        "args": {"type": "object", "description": "That action's arguments."},
        # `secret` is not here: it is a parameter of `write`, so it lives in
        # `args` and is described by that action's own schema. A step key would
        # have been a second place to say it, and the two would drift.
        "id": {"type": "string"},
        "note": {"type": "string"},
        "onError": {"type": "string", "enum": ["abort", "continue"]},
        "return": {"type": "boolean"},
    },
}

# Said once, because a flow's document is described twice — as what a read
# returns and as what a save takes — and the save side is the one that forgot it
# (Copilot, #37).
FLOW_TIMEOUT = {
    "type": "integer",
    "minimum": 1,
    "description": (
        "Seconds the whole run may take before no further step starts. Defaults "
        "to 120; a step already running is bounded by its own wait_timeout."
    ),
}

FLOW_SCHEMAS = {
    "FlowStep": FLOW_STEP,
    "Flow": {
        "type": "object",
        "required": ["name", "steps"],
        "properties": {
            "name": {"type": "string"},
            "workspace": {
                "type": "string",
                "description": "The library it was read from.",
            },
            "shared": {
                "type": "boolean",
                "description": "True when it came from the shared global library.",
            },
            "description": {"type": "string"},
            "parameters": {
                "type": "object",
                "description": "JSON Schema for the values a run accepts.",
            },
            "timeout": FLOW_TIMEOUT,
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/components/schemas/FlowStep"},
            },
        },
    },
    "FlowSummary": {
        "type": "object",
        "description": "One entry in a listing. Never carries the steps.",
        "properties": {
            "name": {"type": "string"},
            "workspace": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {"type": "object"},
            "step_count": {"type": "integer"},
            "shared": {
                "type": "boolean",
                "description": "True when it came from the shared global library.",
            },
        },
    },
    "FlowList": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "count": {"type": "integer"},
            "flows": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/FlowSummary"},
            },
        },
    },
    "FlowSaved": {
        "type": "object",
        "properties": {
            "saved": {"type": "boolean"},
            "workspace": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {"type": "object"},
            "timeout": FLOW_TIMEOUT,
            "warnings": {
                "type": "array",
                "description": (
                    "Things that are valid and probably not what was meant — a "
                    "flow whose first step acts on whatever page the browser "
                    "happens to be on. Present only when there are any; the "
                    "flow is saved either way."
                ),
                "items": {"type": "string"},
            },
            "steps": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/FlowStep"},
            },
        },
    },
    "FlowRun": {
        "type": "object",
        "properties": {
            "flow": {"type": "string"},
            "workspace": {"type": "string"},
            "status": {"type": "string", "enum": ["ok", "failed"]},
            "hint": {
                "type": "object",
                "description": (
                    "On a failed run: where to read about this kind of failure "
                    "and which prompt repairs it. `read` is absent when the "
                    "skill is not being served."
                ),
                "properties": {
                    "read": {
                        "type": "string",
                        "description": "A skill:// resource URI, exactly as read.",
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "The heading in that reference, when there is one."
                        ),
                    },
                    "prompt": {"type": "string"},
                    "arguments": {"type": "object"},
                },
            },
            "steps_run": {"type": "integer"},
            "steps_total": {"type": "integer"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer"},
                        "id": {"type": "string"},
                        "tool": {"type": "string"},
                        "ok": {"type": "boolean"},
                        "summary": {"type": "string"},
                        "note": {"type": "string"},
                        "error": {"type": "string"},
                        "url": {
                            "type": "string",
                            "description": (
                                "The page this step ended on, present only when "
                                "it differs from the step before — so silence "
                                "means the page did not change. Withheld when "
                                "the URL could carry a value typed from a "
                                "secret."
                            ),
                        },
                        "file": {
                            "allOf": [
                                {"$ref": "#/components/schemas/FileEntry"}
                            ],
                            "description": (
                                "The file this step produced — a screenshot, a "
                                "PDF — present whether or not the step is "
                                "marked `return`, because a capture nobody can "
                                "find cannot show anyone what it saw. Withheld "
                                "on the same terms as `url`."
                            ),
                        },
                        "result": {
                            "type": "object",
                            "description": (
                                "Present for a step marked return: true, or "
                                "every step when verbose was set. This is the "
                                "only place a result appears: a run answers "
                                "with the steps that said they were the "
                                "answer, and any number of them may."
                            ),
                        },
                    },
                },
            },
            "url": {"type": "string"},
            "title": {"type": "string"},
            "url_redacted": {
                "type": "boolean",
                "description": (
                    "Present, and true, only when the page the run ended on "
                    "carried a value typed from a secret: `url` is scrubbed, is "
                    "not a real address, and must not be navigated back to."
                ),
            },
            # Present only when the run's browser was reopened after a reap.
            "site_data": SITE_DATA_HINT,
        },
    },
    "FlowDeleted": {
        "type": "object",
        "properties": {
            "deleted": {
                "type": "boolean",
                "description": (
                    "False when there was no such flow, which is not an error."
                ),
            },
            "workspace": {"type": "string"},
            "name": {"type": "string"},
        },
    },
}

_WORKSPACE = {
    "workspace": {
        "type": "string",
        "description": (
            "Whose library. Defaults to the caller's workspace name if the "
            "request carries one, else the shared 'global' library."
        ),
    }
}

_WRITE_WORKSPACE = {
    "workspace": {
        "type": "string",
        "description": (
            "Whose library to write to. Needed unless the request names a "
            "workspace another way, with the X-Workspace header: the shared "
            "'global' library is read-only, so a write that resolves to it is "
            "refused rather than defaulted."
        ),
    }
}

_FLOW_OPERATIONS = {
    "list": (
        "listFlows",
        "Every flow this workspace can run.",
        "Its own, plus the shared global library. A flow of its own wins a name "
        "collision, and each entry says which library it came from.",
        {"type": "object", "properties": dict(_WORKSPACE)},
        "FlowList",
    ),
    "get": (
        "getFlow",
        "One saved flow, with its steps.",
        "Falls back to the shared library when this workspace has no flow of that "
        "name.",
        {
            "type": "object",
            "required": ["name"],
            "properties": {**_WORKSPACE, "name": {"type": "string"}},
        },
        "Flow",
    ),
    "save": (
        "saveFlow",
        "Create or replace a flow.",
        "The same name updates, a new one creates. Always writes to this "
        "workspace's own library, never the shared one — so `workspace` is "
        "required in practice: the shared `global` library is read-only, because "
        "every workspace lists and runs what is in it. The document is validated "
        "against the live tools and a refusal lists every problem at once.",
        {
            "type": "object",
            "required": ["name", "steps"],
            "properties": {
                **_WRITE_WORKSPACE,
                "name": {"type": "string"},
                "description": {"type": "string"},
                "parameters": {"type": "object"},
                "timeout": FLOW_TIMEOUT,
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"$ref": "#/components/schemas/FlowStep"},
                },
            },
        },
        "FlowSaved",
    ),
    "delete": (
        "deleteFlow",
        "Delete one of this workspace's flows.",
        "Deleting one that is not there is not an error. A flow in the shared "
        "`global` library is not yours to delete — every workspace runs those, so "
        "one vanishing mid-run would break somebody else's work — and asking is "
        "refused rather than silently ignored.",
        {
            "type": "object",
            "required": ["name"],
            "properties": {**_WRITE_WORKSPACE, "name": {"type": "string"}},
        },
        "FlowDeleted",
    ),
    "run": (
        "runFlow",
        "Run a saved flow.",
        "Every step, in order, server-side, against this workspace's browser. "
        "Stops at the first failing step unless that step says "
        "onError: continue, and reports which step stopped it and what page the "
        "browser was on. Returns a line per step; pass verbose for every step's "
        "full result.",
        {
            "type": "object",
            "required": ["name"],
            "properties": {
                "params": {
                    "type": "object",
                    "description": "The values this flow declares.",
                },
                "verbose": {"type": "boolean", "default": False},
            },
        },
        "FlowRun",
    ),
    "schema": (
        "flowSchema",
        "The shape of a flow document.",
        "Every tool that may be a step and the parameters each takes, derived "
        "from the live tools — so it cannot describe a step that would not run.",
        {"type": "object", "properties": {}},
        None,
    ),
}


SECRET_SCHEMAS = {
    "SecretEntry": {
        "type": "object",
        "description": "One secret a caller may bind. Never its value.",
        "properties": {
            "name": {"type": "string"},
            "keys": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
            "allowed_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The sites it may be typed on. Empty means anywhere only "
                    "when restricted is false; a declaration that named no "
                    "usable site allows nowhere."
                ),
            },
            "restricted": {
                "type": "boolean",
                "description": (
                    "Whether the secret declares where it may be used. True with "
                    "no allowed_urls is usable nowhere."
                ),
            },
            "allowed_urls_rejected": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Declared sites that did not parse. Present only when there "
                    "are any, and the secret cannot be used until they are fixed."
                ),
            },
            "origins": {
                "type": "array",
                "description": (
                    "Where this secret's policy and keys came from, in the "
                    "order they were applied."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "location": {"type": "string"},
                    },
                },
            },
            "key_sources": {
                "type": "object",
                "description": "Where each key's value is read from.",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "path": {"type": "string"},
                        "name": {"type": "string"},
                    },
                },
            },
            "keys_unresolved": {
                "type": "array",
                "description": (
                    "Config-defined keys with no value right now. Present only "
                    "when there are any; a bind of one is refused with the reason."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "inline_keys": {
                "type": "array",
                "description": (
                    "Keys whose value is written directly in the config. "
                    "Present only when there are any."
                ),
                "items": {"type": "string"},
            },
        },
    },
    "SecretList": {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "workspace": {"type": "string"},
            "secrets": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/SecretEntry"},
            },
        },
    },
}

FILE_SCHEMAS = {
    "FileEntry": {
        "type": "object",
        "description": (
            "One file, in whichever of the four sections listed it — Files, "
            "Screenshots, Recordings or Downloads."
        ),
        "properties": {
            "name": {"type": "string"},
            "uri": {
                "type": "string",
                "description": (
                    "This file's address. keep_file and upload_file(file=) "
                    "take it."
                ),
            },
            "size": {"type": "integer"},
            "created": {
                "type": ["integer", "null"],
                "description": "When it was written, in epoch milliseconds.",
            },
            "content_type": {"type": "string"},
            "image": {
                "type": "boolean",
                "description": "Whether it can be displayed inline.",
            },
            "keep_with": {
                "type": "string",
                "description": (
                    "Present on a screenshot or a download, never on a file "
                    "already in Files: the MCP call that keeps a copy which "
                    "outlives the browser. Until it is made, a screenshot's "
                    "url stops working once cleared and a download's once the "
                    "browser ends. The HTTP equivalent is "
                    "PUT /files/{folder}/{name}/kept."
                ),
            },
            "url": {
                "type": "string",
                "description": (
                    "Signed and time-limited; needs no bearer token. Absolute "
                    "when PUBLIC_BASE_URL is set, else a path on this server."
                ),
            },
        },
    },
    "FileList": {
        "type": "object",
        "description": "workspace://files: Files' own listing, and its three folders.",
        "properties": {
            "workspace": {
                "type": ["string", "null"],
                "description": "The workspace these files belong to.",
            },
            "count": {"type": "integer"},
            "files": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/FileEntry"},
            },
            "folders": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/FileFolder"},
            },
        },
    },
    "FileFolder": {
        "type": "object",
        "description": "One of the three folders named beside Files' own files.",
        "properties": {
            "name": {
                "type": "string",
                "enum": ["screenshots", "recordings", "downloads"],
            },
            "uri": {"type": "string"},
            "count": {"type": "integer"},
            "browser": {
                "type": "boolean",
                "description": (
                    "Present on downloads only: whether a browser is open to "
                    "read them from."
                ),
            },
        },
    },
    "FolderList": {
        "type": "object",
        "description": (
            "One folder's own listing: workspace://files/screenshots, /recordings "
            "or /downloads."
        ),
        "properties": {
            "workspace": {"type": ["string", "null"]},
            "folder": {
                "type": "string",
                "enum": ["screenshots", "recordings", "downloads"],
            },
            "uri": {"type": "string"},
            "count": {"type": "integer"},
            "browser": {
                "type": "boolean",
                "description": (
                    "Present on downloads only: whether a browser is open to "
                    "read them from."
                ),
            },
            "files": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/FileEntry"},
            },
        },
    },
    "FileKept": {
        "type": "object",
        "properties": {
            "kept": {"type": "boolean"},
            "from": {"type": "string", "description": "The uri it was kept from."},
            "uri": {"type": "string", "description": "Its new address, in Files."},
            "name": {"type": "string"},
            "size": {"type": "integer"},
            "created": {"type": ["integer", "null"]},
        },
    },
}


def _storage(kind: str) -> dict:
    """localStorage and sessionStorage per origin of one host: key counts
    (``integer``) in a listing, the keys themselves (``object``) in full."""
    return {
        "type": "array",
        "description": (
            "Per origin: the same host on another port or scheme is another "
            "origin. sessionStorage is the one tab's, for the page the save was "
            "made on."
        ),
        "items": {
            "type": "object",
            "required": ["origin", "local_storage", "session_storage"],
            "properties": {
                "origin": {"type": "string"},
                "local_storage": {"type": kind},
                "session_storage": {"type": kind},
            },
        },
    }


SITE_DATA_SCHEMAS = {
    "SiteList": {
        "type": "object",
        "description": (
            "workspace://site-data: one entry per site the last save holds data "
            "for, counts only, the sites the workspace went to first."
        ),
        "properties": {
            "sites": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["site", "uri", "cookies", "storage"],
                    "properties": {
                        "site": {"type": "string", "description": "A host."},
                        "uri": {"type": "string"},
                        "cookies": {
                            "type": "integer",
                            "description": (
                                "Its own cookies and the parent-domain ones "
                                "that reach it."
                            ),
                        },
                        "storage": _storage("integer"),
                    },
                },
            },
            "saved_at": {
                "type": ["number", "null"],
                "description": "When the snapshot was saved, in epoch seconds.",
            },
            "uri": {"type": "string"},
        },
    },
    "SiteData": {
        "type": "object",
        "description": (
            "workspace://site-data/{site}: one site in full. An httpOnly cookie's "
            "value is shown as \u2022\u2022\u2022."
        ),
        "properties": {
            "site": {"type": "string"},
            "uri": {"type": "string"},
            "cookies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "value": {"type": "string"},
                        "domain": {"type": "string"},
                        "path": {"type": "string"},
                        "expiry": {"type": ["integer", "null"]},
                        "http_only": {"type": "boolean"},
                        "secure": {"type": "boolean"},
                        "same_site": {"type": ["string", "null"]},
                        "shared": {"type": "boolean"},
                    },
                },
            },
            "storage": _storage("object"),
            "own_cookies": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Cookies this site owns (host or .host): what Forget removes."
                ),
            },
            "kept_shared": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "domain", "path"],
                    "properties": {
                        "name": {"type": "string"},
                        "domain": {"type": "string"},
                        "path": {"type": "string"},
                    },
                },
                "description": (
                    "Parent-domain cookies it only sits under: Forget keeps them."
                ),
            },
        },
    },
}

_FILE_OPERATIONS = {
    "list": (
        "listFiles",
        "Files' own listing, and its three folders.",
        "Everything kept in Files, newest first, plus a count for "
        "workspace://files/screenshots, workspace://files/recordings and "
        "workspace://files/downloads — each its "
        "own listing, fetched separately so a caller who only wants to know "
        "whether there is anything to look at need not pay for either.",
        {"type": "object", "properties": {}},
        "FileList",
    ),
    "screenshots": (
        "listScreenshots",
        "This workspace's saved screenshots, not yet kept.",
        "Newest first. Works after the browser that took them is gone, because "
        "they are already ours.",
        {"type": "object", "properties": {}},
        "FolderList",
    ),
    "recordings": (
        "listRecordings",
        "This workspace's recordings, not yet kept.",
        "Newest first. One video per browser opened with record=true, filed "
        "shortly after it ended. Works after the browser is gone.",
        {"type": "object", "properties": {}},
        "FolderList",
    ),
    "downloads": (
        "listDownloads",
        "This session's browser downloads.",
        "Newest first, read from the browser itself. Answers empty rather than "
        "failing when there is no browser open; a Grid failure from one that is "
        "open is a real fault and is not hidden.",
        {"type": "object", "properties": {}},
        "FolderList",
    ),
    "keep": (
        "keepFile",
        "Moves a screenshot or a recording into Files, or copies a download there.",
        "The folder and name are both in the path — a screenshot moves out of "
        "its folder, a download is copied because the Grid offers no way to "
        "remove one file. A clash with a name already in Files lands beside it "
        "as name (1) for a screenshot; a download REPLACES it.",
        {"type": "object", "properties": {}},
        "FileKept",
    ),
}
