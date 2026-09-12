"""The HTTP transport: the line server's replies over POST, the page beside it, the bridge."""
import http.client
import json
import os
import socket
import threading

import pytest

from cadcore.service import web

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


@pytest.fixture(scope="module")
def served():
    server = web.serve(port=0, root=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def call(server, method, path, payload=None):
    connection = http.client.HTTPConnection(*server.server_address[:2], timeout=120)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
    response = connection.getresponse()
    return response.status, json.loads(response.read().decode("utf-8") or "null"), response


def test_an_operation_answers_the_way_the_line_server_does(served):
    status, reply, _ = call(served, "POST", "/op",
                            {"op": "open", "path": os.path.join(REPO, "examples/bracket.json"), "id": 7})
    assert status == 200 and reply["ok"] and reply["id"] == 7
    status, reply, _ = call(served, "POST", "/op", {"op": "build"})
    assert reply["ok"] and reply["result"]["faces"] > 6
    status, reply, _ = call(served, "POST", "/op", {"op": "nothing_of_the_kind"})
    assert status == 200 and reply["kind"] == "unknown_op"


def test_the_page_and_its_scripts_are_served(served):
    import re

    connection = http.client.HTTPConnection(*served.server_address[:2])
    connection.request("GET", "/")
    response = connection.getresponse()
    page = response.read().decode("utf-8")
    assert response.status == 200 and '<div id="root">' in page
    for asset in re.findall(r'(?:src|href)="(/static/assets/[^"]+)"', page):   # the build's own names
        connection.request("GET", asset)
        response = connection.getresponse()
        assert response.status == 200 and len(response.read()) > 1000, asset
    connection.request("GET", "/static/../web.py")             # never outside the folder
    assert connection.getresponse().status == 404


def test_the_examples_are_listed_by_name(served):
    _, reply, _ = call(served, "GET", "/examples")
    names = {e["name"] for e in reply["examples"]}
    assert "four-bar linkage" in names and all(e["path"].endswith(".json") for e in reply["examples"])


def test_bad_json_is_answered_not_crashed(served):
    connection = http.client.HTTPConnection(*served.server_address[:2])
    connection.request("POST", "/op", b"{not json", {"Content-Type": "application/json"})
    response = connection.getresponse()
    assert response.status == 400 and json.loads(response.read())["kind"] == "bad_json"


def test_the_bridge_admits_the_token_and_answers_by_id(served):
    hub = served.hub
    hub.listen()
    token = open(hub.token_file, encoding="utf-8").read()
    with socket.create_connection(("127.0.0.1", hub.bridge_port)) as connection:
        stream = connection.makefile("rw", encoding="utf-8")
        stream.write(json.dumps({"token": "wrong"}) + "\n")
        stream.flush()
        assert json.loads(stream.readline())["kind"] == "not_admitted"
    with socket.create_connection(("127.0.0.1", hub.bridge_port)) as connection:
        stream = connection.makefile("rw", encoding="utf-8")
        stream.write(json.dumps({"token": token}) + "\n")
        stream.flush()
        assert json.loads(stream.readline())["admitted"]
        for number, request in ((2, {"op": "open", "path": os.path.join(REPO, "examples/bracket.json")}),
                                (3, {"op": "describe_document"})):
            stream.write(json.dumps(dict(request, id=number)) + "\n")
            stream.flush()
            reply = json.loads(stream.readline())
            assert reply["ok"] and reply["id"] == number, reply
        assert reply["result"]["features"]


def test_an_ask_without_the_assistant_installed_says_so(served, monkeypatch):
    monkeypatch.setenv("CADCORE_ASSISTANT", "no-such-assistant-cli")
    _, reply, _ = call(served, "POST", "/ask", {"question": "a hole here", "situation": ""})
    assert not reply["ok"] and reply["kind"] == "missing_dependency"


def test_only_the_page_may_post(served):
    """A form on another site sends text/plain or another Origin; both are refused."""
    connection = http.client.HTTPConnection(*served.server_address[:2])
    body = json.dumps({"op": "describe_document"}).encode("utf-8")
    connection.request("POST", "/op", body, {"Content-Type": "text/plain"})
    response = connection.getresponse()
    assert response.status == 415 and json.loads(response.read())["kind"] == "not_admitted"
    connection.request("POST", "/op", body, {"Content-Type": "application/json",
                                             "Origin": "http://evil.example"})
    response = connection.getresponse()
    assert response.status == 403 and json.loads(response.read())["kind"] == "not_admitted"
    connection.request("POST", "/op", body, {"Content-Type": "application/json",
                                             "Host": "cad.example:8080"})
    response = connection.getresponse()
    assert response.status == 403
    own = "http://127.0.0.1:%d" % served.server_address[1]
    connection.request("POST", "/op", body, {"Content-Type": "application/json", "Origin": own})
    response = connection.getresponse()
    assert response.status == 200 and "ok" in json.loads(response.read())


def test_the_generation_counts_edits_not_questions(served):
    hub = served.hub
    call(served, "POST", "/op", {"op": "open", "path": os.path.join(REPO, "examples/bracket.json")})
    was = hub.generation
    call(served, "POST", "/op", {"op": "describe_document"})
    call(served, "POST", "/op", {"op": "build"})
    assert hub.generation == was
    _, reply, _ = call(served, "POST", "/op", {"op": "set_parameter", "name": "thickness", "value": 7})
    assert reply["ok"] and hub.generation == was + 1


def test_a_fenced_page_still_opens_the_gallery_as_a_copy(tmp_path):
    server = web.serve(port=0, root=str(tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, reply, _ = call(server, "GET", "/examples")
        path = next(e["path"] for e in reply["examples"] if e["path"].endswith("bracket.json"))
        _, reply, _ = call(server, "POST", "/op", {"op": "open", "path": path, "as_copy": True})
        assert reply["ok"] and reply["result"]["saved"] is False
        _, reply, _ = call(server, "POST", "/op", {"op": "save", "path": path})
        assert reply["kind"] == "bad_path", "the copy cannot be written back over the example"
    finally:
        server.shutdown()
