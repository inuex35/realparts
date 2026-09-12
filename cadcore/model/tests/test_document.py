

def test_a_feature_id_cannot_carry_a_name_separator():
    """`a/b` as an id made a face `a/b/+z` that `feature_of` read as `a`'s.

    The separators are how every name is read, so an id holding one breaks
    the promise that a reference means the same thing after a change.
    """
    import pytest

    from cadcore import names
    from cadcore.errors import CadError
    from cadcore.model.document import Document

    for bad in ("a/b", "a:b", "a|b", "a#2", "a@1", "a~3", "a b", ""):
        with pytest.raises(CadError) as refused:
            Document.from_dict({"features": [{"id": bad, "type": "box", "size": [1, 1, 1]}]})
        assert refused.value.kind == "bad_arguments"
    assert names.check_id("plate_2") == "plate_2"
    with pytest.raises(CadError):
        Document.from_dict({"features": [{"type": "box"}]})     # no id at all
    with pytest.raises(CadError):
        Document.from_dict({"features": ["box"]})              # not an object


def test_every_field_is_either_content_or_the_sessions_own():
    """A checkpoint restores the content and nothing else; a new field has
    to say which it is, or a restore silently keeps or clobbers it."""
    import dataclasses

    from cadcore.model.document import Document

    fields = {f.name for f in dataclasses.fields(Document)}
    assert fields == set(Document.CONTENT) | set(Document.SESSIONS_OWN), \
        sorted(fields ^ (set(Document.CONTENT) | set(Document.SESSIONS_OWN)))
    a = Document(parameters={"w": 1}, meta={"name": "a"}, unit="in")
    a.source, a.revision = "/a.json", 7
    b = Document(parameters={"w": 2})
    b.source, b.revision = "/b.json", 9
    b.take_content_from(a)
    assert b.parameters == {"w": 1} and b.meta == {"name": "a"} and b.unit == "in"
    assert b.source == "/b.json" and b.revision == 9
    a.parameters["w"] = 3
    assert b.parameters["w"] == 1                      # a copy, not a shared dict
