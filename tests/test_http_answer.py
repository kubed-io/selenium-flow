"""One decision about what a request is, shared by every JSON tree.

There were four wrappers doing this job — `routes._answer`, `files.answer`,
`flowapi.answer` and `admin.guarded` — each deciding separately what
unauthorised means, what an unreadable body means, and which failures earn a
traceback. Four copies of a decision is four chances for one to drift, and two
of them already disagreed about the log level for an unreachable Grid.

These tests hold the surfaces against each other rather than against a fixture,
because the property worth keeping is that they *agree*, not that any one of
them returns a particular string.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.http import answer

from .conftest import TOKEN

pytestmark = pytest.mark.unit

WORKSPACE = "answer-check"
AUTH = {"Authorization": f"Bearer {TOKEN}", "X-Workspace": WORKSPACE}

# One write on each JSON tree — the browser actions, the files surface, the
# flows surface — with the method each is actually served at. REST means these
# differ: keeping a file is a PUT, saving a flow is a PUT, opening a browser and
# running a flow are POSTs. What must NOT differ is the answer to a request that
# never reaches the handler.
TREES = (
    ("post", "/browser"),
    ("put", "/files/downloads/report.pdf/kept"),
    ("put", "/flows/example"),
    ("post", "/flows/example/runs"),
)


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app())


def test_there_is_one_answer_and_the_trees_share_it():
    """The module exists and is where the shared decision lives."""
    from kubed.selenium_flow.http import answer as answer_module

    assert callable(answer_module.answer)


@pytest.mark.parametrize(("method", "path"), TREES)
def test_no_token_is_401_on_every_tree(client, method, path):
    """Whichever tree answered, a request with no credential reads the same."""
    response = getattr(client, method)(path, json={})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


@pytest.mark.parametrize(("method", "path"), TREES)
def test_a_body_that_is_not_an_object_is_400_on_every_tree(client, method, path):
    """A JSON array is valid JSON and still not a set of arguments."""
    response = getattr(client, method)(path, json=[1, 2, 3], headers=AUTH)
    assert response.status_code == 400
    assert response.json() == {"error": "body must be a JSON object"}


def test_a_failure_becomes_a_status_and_a_message_in_one_place():
    """`errors` decides what a failure MEANS; this turns that into a response.

    The split is deliberate: `errors` stays free of any web framework, because
    it is also what the MCP surface consults, and a status code is not a shape.
    """
    import logging

    from kubed.selenium_flow.http.answer import as_response

    response = as_response(ValueError("no such flow"), "get", logging.getLogger("t"))
    assert response.status_code == 400
    assert bytes(response.body).decode() == '{"error":"no such flow"}'


def test_refused_keeps_a_filesystem_path_out_of_the_body():
    """The admin's one failure shaper scrubs an OSError's path, as the secrets
    and flows routes always did; the files routes had their own copy that
    did not."""
    import logging

    exc = PermissionError(13, "Permission denied", "/data/flows/secret-layout")
    response = answer.refused(exc, "files for x", logging.getLogger("t"))
    body = response.body.decode()
    assert "secret-layout" not in body and "Permission denied" in body


@pytest.mark.parametrize(("method", "path"), TREES)
def test_a_json_body_over_1_mib_is_413_naming_the_cap_on_every_tree(
    client, method, path
):
    big = b'{"x": "' + b"a" * (2**20) + b'"}'
    headers = {**AUTH, "Content-Type": "application/json"}
    response = getattr(client, method)(path, content=big, headers=headers)
    assert response.status_code == 413
    assert response.json() == {"error": "a JSON body is limited to 1 MiB"}


def test_a_json_body_just_under_the_cap_is_read(client):
    body = b'{"x": "' + b"a" * (2**20 - 20) + b'"}'
    headers = {**AUTH, "Content-Type": "application/json"}
    assert client.post("/browser", content=body, headers=headers).status_code != 413


def test_an_upload_has_its_own_larger_cap(client, monkeypatch):
    monkeypatch.setattr(answer, "UPLOAD_CAP", 2**20)
    files = {"file": ("big.bin", b"a" * (2**20 + 1))}
    response = client.post("/browser", files=files, headers=AUTH)
    assert response.status_code == 413
    assert response.json() == {"error": "an upload is limited to 1 MiB"}


def test_a_flow_over_1_mib_is_a_413_over_http_and_a_refusal_over_mcp():
    import asyncio

    from kubed.selenium_flow import errors
    from kubed.selenium_flow.faults import TooLarge
    from kubed.selenium_flow.flows import api as flowapi

    text = "name: big\nsteps: []\n# " + "a" * 2**20
    with pytest.raises(TooLarge, match="1 MiB") as caught:
        asyncio.run(flowapi.save_text(None, "s", "big", text, None))
    assert errors.status_for(caught.value) == 413
    assert isinstance(caught.value, ValueError), "MCP reads it as any refusal"
