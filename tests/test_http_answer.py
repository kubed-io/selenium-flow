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

from .conftest import TOKEN

pytestmark = pytest.mark.unit

SESSION = "answer-check"
AUTH = {"Authorization": f"Bearer {TOKEN}", "X-Session-Key": SESSION}

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
