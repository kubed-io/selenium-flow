"""The HTTP route span: one SERVER span per request, named by its route template."""

import logging
from contextlib import contextmanager

import pytest
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.testclient import TestClient

from kubed.selenium_flow.http.access_log import RouteTemplates
from kubed.selenium_flow.http.tracing import RouteSpans

from .conftest import recorded

pytestmark = pytest.mark.unit

SEEN: list[int] = []


async def history(request):
    SEEN.append(trace.get_current_span().get_span_context().span_id)
    return PlainTextResponse("ok")


async def boom(request):
    return PlainTextResponse("down", status_code=503)


async def crash(request):
    raise RuntimeError("at https://example.com/secret")


async def stream(request):
    async def body():
        yield b"first"
        raise RuntimeError("at https://example.com/midway")

    return StreamingResponse(body())


async def plain(request):
    return PlainTextResponse("ok")


async def live(websocket):
    await websocket.accept()
    await websocket.close()


ROUTES = [
    Route("/workspaces/{key}/history", history),
    Route("/boom", boom),
    Route("/crash", crash),
    Route("/stream", stream),
    Route("/health", plain),
    Route("/mcp", plain),
    WebSocketRoute("/live/{key}", live),
    Mount("/flow", routes=[Route("/health", plain), Route("/mcp", plain)]),
]


@pytest.fixture
def rec():
    return recorded()


@pytest.fixture
def seen():
    SEEN.clear()
    return SEEN


@pytest.fixture
def app(rec, seen):
    return Starlette(
        routes=ROUTES,
        middleware=[
            Middleware(
                RouteSpans,
                tracer=rec.telemetry.tracer,
                templates=RouteTemplates(ROUTES),
                prefix="/flow",
            )
        ],
    )


def test_a_request_span_is_named_by_its_route_template(app, rec):
    TestClient(app).get("/workspaces/acme/history?workspace=acme&sig=s1gn4ture")
    [span] = rec.spans.get_finished_spans()
    assert span.name == "GET /workspaces/{key}/history" and span.kind is SpanKind.SERVER
    assert dict(span.attributes) == {
        "http.request.method": "GET",
        "http.route": "/workspaces/{key}/history",
        "http.response.status_code": 200,
    }
    assert "acme" not in repr(span.to_json()) and "s1gn4ture" not in repr(span.to_json())


def test_the_route_span_is_current_for_the_route(app, rec, seen):
    TestClient(app).get("/workspaces/acme/history")
    [span] = rec.spans.get_finished_spans()
    assert seen == [span.context.span_id]


def test_a_path_no_route_serves_is_named_by_its_method_alone(app, rec):
    TestClient(app).get("/acme-workspace/x")
    [span] = rec.spans.get_finished_spans()
    assert span.name == "GET" and "http.route" not in span.attributes
    assert span.attributes["http.response.status_code"] == 404


def test_a_5xx_is_an_error_and_a_2xx_is_not(app, rec):
    client = TestClient(app)
    client.get("/boom"), client.get("/workspaces/a/history")
    by_status = {
        s.attributes["http.response.status_code"]: s.status for s in rec.spans.get_finished_spans()
    }
    assert by_status[503].status_code is StatusCode.ERROR
    assert by_status[200].status_code is StatusCode.UNSET


def test_an_app_that_raises_ends_its_span_as_a_500(app, rec):
    with pytest.raises(RuntimeError, match=r"example\.com/secret"):
        TestClient(app).get("/crash")
    [span] = rec.spans.get_finished_spans()
    assert span.attributes["http.response.status_code"] == 500
    assert span.status.status_code is StatusCode.ERROR and span.status.description is None
    assert len(span.events) == 0 and "secret" not in repr(span.to_json())


@pytest.mark.parametrize("path", ["/health", "/mcp", "/flow/health", "/flow/mcp"])
def test_the_probes_and_mcp_have_no_span(app, rec, path):
    TestClient(app).get(path)
    assert rec.spans.get_finished_spans() == ()


def test_a_websocket_has_no_span(app, rec):
    with TestClient(app).websocket_connect("/live/acme"):
        pass
    assert rec.spans.get_finished_spans() == ()


def test_a_traceparent_continues_its_trace(app, rec):
    TestClient(app).get(
        "/boom",
        headers={"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"},
    )
    [span] = rec.spans.get_finished_spans()
    assert span.context.trace_id == 0x0AF7651916CD43DD8448EB211C80319C
    assert span.parent.span_id == 0xB7AD6B7169203331


def test_matched_is_the_template_or_none():
    templates = RouteTemplates(ROUTES)
    assert templates.matched("/workspaces/acme/history") == "/workspaces/{key}/history"
    assert templates.matched("/acme-workspace/x") is None
    assert templates.template("/acme-workspace/x") == "/acme-workspace/…"  # the log's, unchanged


def test_an_app_that_raises_after_its_answer_began_is_an_error_at_that_status(app, rec):
    with pytest.raises(RuntimeError, match=r"example\.com/midway"):
        TestClient(app).get("/stream")
    [span] = rec.spans.get_finished_spans()
    assert span.attributes["http.response.status_code"] == 200
    assert span.status.status_code is StatusCode.ERROR and span.status.description is None


class BrokenSpan:
    def set_attribute(self, *_):
        raise ValueError("at https://example.com/secret")

    def set_status(self, *_):
        raise ValueError("at https://example.com/secret")


class BrokenTracer:
    """A tracer whose span fails at the end (`start` False) or never starts."""

    def __init__(self, start):
        self.start = start

    @contextmanager
    def start_as_current_span(self, *_, **__):
        if not self.start:
            raise ValueError("at https://example.com/secret")
        yield BrokenSpan()


def served(tracer):
    return Starlette(
        routes=ROUTES,
        middleware=[
            Middleware(RouteSpans, tracer=tracer, templates=RouteTemplates(ROUTES))
        ],
    )


def test_a_tracer_that_cannot_start_a_span_leaves_the_route_alone(caplog, seen):
    with caplog.at_level(logging.WARNING):
        response = TestClient(served(BrokenTracer(start=False))).get(
            "/workspaces/acme/history"
        )
    assert response.status_code == 200 and len(seen) == 1
    assert "telemetry: a route span failed (ValueError)" in caplog.text
    assert "secret" not in caplog.text


def test_a_span_that_fails_at_its_end_replaces_neither_answer_nor_exception(caplog):
    client = TestClient(served(BrokenTracer(start=True)))
    assert client.get("/workspaces/acme/history").status_code == 200
    with pytest.raises(RuntimeError, match=r"example\.com/secret"):
        client.get("/crash")


def test_a_failing_propagator_leaves_the_route_alone(app, rec, seen, monkeypatch):
    def failing(*_, **__):
        raise ValueError("at https://example.com/secret")

    monkeypatch.setattr("kubed.selenium_flow.http.tracing.propagate.extract", failing)
    assert TestClient(app).get("/workspaces/acme/history").status_code == 200
    assert len(seen) == 1 and rec.spans.get_finished_spans() == ()


def test_a_method_outside_the_known_nine_is_other(app, rec):
    TestClient(app).request("ACME-WORKSPACE", "/boom")
    TestClient(app).request("PATCH", "/boom")
    odd, patch = rec.spans.get_finished_spans()
    assert odd.name == "_OTHER /boom" and odd.attributes["http.request.method"] == "_OTHER"
    assert patch.attributes["http.request.method"] == "PATCH"
