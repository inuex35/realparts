"""The resources this server offers.

Guards: the document schema is generated, so every shipped example validates
against it; and a reader names a URI, never a path, so the lookup cannot
reach outside the catalogue.
"""
from __future__ import annotations

import glob
import json
import os

import pytest

from cadcore.service import resources
from cadcore.service.mcp import respond
from cadcore.ops.session import Session

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _ask(method, **params):
    return respond(Session(), {"jsonrpc": "2.0", "id": 1, "method": method,
                               "params": params})


def test_the_catalogue_is_the_directory_rather_than_a_list_of_it():
    offered = {r["uri"] for r in resources.catalogue()}
    for path in glob.glob(os.path.join(HERE, "examples", "*.json")):
        stem = os.path.splitext(os.path.basename(path))[0]
        assert "cad://example/%s" % stem in offered
    for path in glob.glob(os.path.join(HERE, "docs", "*.md")):
        stem = os.path.splitext(os.path.basename(path))[0]
        assert "cad://doc/%s" % stem in offered


def test_everything_offered_can_be_read():
    for entry in resources.catalogue():
        got = resources.read(entry["uri"])
        assert got["uri"] == entry["uri"]
        assert got["text"].strip(), entry["uri"]


def test_a_uri_is_a_key_and_not_a_path():
    """Guards: a URI is looked up, never joined onto a directory."""
    for attempt in ("cad://doc/../../etc/passwd",
                    "cad://doc//etc/passwd",
                    "cad://example/../cadcore/mcp",
                    "file:///etc/passwd",
                    "cad://doc/features/../../../../etc/passwd"):
        with pytest.raises(KeyError):
            resources.read(attempt)


def test_an_unknown_uri_says_what_there_is():
    with pytest.raises(LookupError) as raised:
        _ask("resources/read", uri="cad://doc/nonesuch")
    assert "cad://doc/features" in str(raised.value)


def test_the_server_advertises_what_it_answers():
    said = _ask("initialize")["capabilities"]
    assert "resources" in said
    assert _ask("resources/list")["resources"]
    assert _ask("resources/templates/list") == {"resourceTemplates": []}
    read = _ask("resources/read", uri="cad://example/bracket")["contents"][0]
    assert json.loads(read["text"])["features"]


def test_the_schema_describes_this_kernel_and_not_a_previous_one():
    """Guards: every shipped example validates against the published schema."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = resources.document_schema()
    check = jsonschema.Draft202012Validator(schema)
    for path in sorted(glob.glob(os.path.join(HERE, "examples", "*.json"))):
        with open(path, encoding="utf-8") as file:
            document = json.load(file)
        errors = sorted(check.iter_errors(document), key=lambda e: list(e.path))
        assert not errors, "%s: %s" % (
            os.path.basename(path),
            "; ".join("%s %s" % (list(e.path), e.message) for e in errors[:3]))


def test_the_schema_refuses_what_the_kernel_refuses():
    jsonschema = pytest.importorskip("jsonschema")
    check = jsonschema.Draft202012Validator(resources.document_schema())
    # an argument no feature declares
    assert not check.is_valid(
        {"features": [{"id": "b", "type": "box", "size": [1, 2, 3],
                       "colour": "red"}]})
    # a feature type that does not exist
    assert not check.is_valid({"features": [{"id": "x", "type": "teleport"}]})
    # a required argument missing
    assert not check.is_valid({"features": [{"id": "b", "type": "box"}]})
    # an expression where a number goes is allowed
    assert check.is_valid(
        {"parameters": {"w": 10}, "features": [
            {"id": "b", "type": "box", "size": ["w", "w * 2", 3]}]})


def test_every_feature_type_is_in_the_schema():
    from cadcore import features

    features.load()
    named = {one["title"] for one
             in resources.document_schema()["properties"]["features"]["items"]["oneOf"]}
    assert named == set(features.types())
