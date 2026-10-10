"""BiDi events as a browser sends them, for feeding `capture.store.Capture`.

Each builder returns ``(method, params)`` in WebDriver BiDi's shape, so a test
reads as the stream a browser would deliver: ``feed(capture, nav_started(...),
req(...), done(...))``. Only the fields capture reads are filled in, plus the
ones it must never read (``cookies``, ``goog:postData``), so a test can show
they never reach a row.
"""

from __future__ import annotations


def _headers(pairs) -> list[dict]:
    return [{"name": n, "value": {"type": "string", "value": v}} for n, v in pairs]


def log(
    text,
    level="info",
    type="console",
    ctx="top-1",
    ts=1000.0,
    realm="r1",
    frame=("https://a.test/app.js", 141, 3),
):
    params = {
        "type": type,
        "level": level,
        "text": text,
        "timestamp": ts,
        "source": {"realm": realm, "context": ctx},
    }
    if type == "console":
        params["method"] = {"error": "error", "warn": "warn"}.get(level, "log")
        params["args"] = [{"type": "string", "value": text}]
    if frame is not None:
        url, line, column = frame
        params["stackTrace"] = {
            "callFrames": [
                {
                    "url": url,
                    "functionName": "f",
                    "lineNumber": line,
                    "columnNumber": column,
                }
            ]
        }
    return "log.entryAdded", params


def nav_started(url, ctx="top-1", ts=1000.0, navigation="nav-1"):
    return "browsingContext.navigationStarted", {
        "context": ctx,
        "navigation": navigation,
        "timestamp": ts,
        "url": url,
    }


def nav_event(method, url, ctx="top-1", ts=1000.0):
    """``navigationCommitted``, ``historyUpdated`` or ``fragmentNavigated``."""
    return f"browsingContext.{method}", {
        "context": ctx,
        "navigation": "nav-1",
        "timestamp": ts,
        "url": url,
    }


def context_created(ctx, parent=None, url="about:blank"):
    return "browsingContext.contextCreated", {
        "context": ctx,
        "parent": parent,
        "url": url,
        "children": None,
        "userContext": "default",
    }


def context_destroyed(ctx, parent=None):
    return "browsingContext.contextDestroyed", {
        "context": ctx,
        "parent": parent,
        "url": "about:blank",
        "children": None,
        "userContext": "default",
    }


def req(
    rid,
    url,
    *,
    ctx="top-1",
    dest="",
    initiator="fetch",
    method="GET",
    headers=(),
    nav=None,
    redirect=0,
    ts=1000.0,
):
    return "network.beforeRequestSent", {
        "context": ctx,
        "navigation": nav,
        "redirectCount": redirect,
        "request": {
            "request": rid,
            "url": url,
            "method": method,
            "headers": _headers(headers),
            "cookies": [
                {"name": "sid", "value": {"type": "string", "value": "COOKIE-VALUE"}}
            ],
            "headersSize": 0,
            "bodySize": 0,
            "destination": dest,
            "initiatorType": initiator,
            "timings": {},
            "goog:postData": "POST-DATA",
        },
        "timestamp": ts,
        "isBlocked": False,
    }


def done(
    rid,
    status,
    *,
    mime="application/json",
    body_size=10,
    headers=(),
    redirect=0,
    ts=1010.0,
    ctx="top-1",
    url="",
):
    response = {
        "url": url,
        "protocol": "http/1.1",
        "status": status,
        "statusText": {200: "OK", 302: "Found", 404: "Not Found"}.get(status, ""),
        "fromCache": False,
        "headers": _headers(headers),
        "mimeType": mime,
        "bytesReceived": (body_size or 0) + 200,
        "headersSize": 200,
    }
    if body_size is not None:
        response["bodySize"] = body_size
    return "network.responseCompleted", {
        "context": ctx,
        "navigation": None,
        "redirectCount": redirect,
        "request": {"request": rid, "url": url, "method": "GET", "headers": []},
        "timestamp": ts,
        "response": response,
    }


def fail(rid, error="net::ERR_FAILED", ts=1010.0, ctx="top-1", redirect=0, url=""):
    return "network.fetchError", {
        "context": ctx,
        "navigation": None,
        "redirectCount": redirect,
        "request": {"request": rid, "url": url, "method": "GET", "headers": []},
        "timestamp": ts,
        "errorText": error,
    }
