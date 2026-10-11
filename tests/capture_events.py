"""BiDi events as a browser sends them, for feeding `capture.store.Capture`.

Each builder returns ``(method, params)`` in WebDriver BiDi's shape, so a test
reads as the stream a browser would deliver: ``feed(capture, nav_started(...),
req(...), done(...))``. Only the fields capture reads are filled in, plus the
ones it must never show (``cookies``, whose values it only learns to mask, and
``goog:postData``), so a test can show they never reach a row.
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


def chrome_timings(origin, end):
    """Chrome's ``request.timings`` (measured): ``timeOrigin`` is the epoch ms
    the request started, and every other field is ms relative to it, with
    ``requestTime`` 0. Chrome also stamps every event of the request with that
    start, so its events' ``timestamp`` say nothing about how long it took."""
    return {"timeOrigin": origin, "requestTime": 0, "requestStart": end * 0.7,
            "responseStart": end * 0.99, "responseEnd": end}


def firefox_timings(start, end):
    """Firefox's (measured): ``timeOrigin`` 0, every field epoch ms."""
    return {"timeOrigin": 0, "requestTime": start, "requestStart": start + 1,
            "responseStart": end - 1, "responseEnd": end}


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
    timings=None,
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
        "request": _ended(rid, url, timings),
        "timestamp": ts,
        "response": response,
    }


def fail(
    rid, error="net::ERR_FAILED", ts=1010.0, ctx="top-1", redirect=0, url="", timings=None
):
    return "network.fetchError", {
        "context": ctx,
        "navigation": None,
        "redirectCount": redirect,
        "request": _ended(rid, url, timings),
        "timestamp": ts,
        "errorText": error,
    }


def _ended(rid, url, timings):
    request = {"request": rid, "url": url, "method": "GET", "headers": []}
    if timings is not None:
        request["timings"] = timings
    return request
