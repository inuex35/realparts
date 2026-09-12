"""Work that outlives the kernel that made it.

The kernel runs in its own process so an OCCT crash cannot take the editor
down with it. That argument only holds if the *work* survives too, and none of
it did: an edit lived in the kernel's memory alone, the add-on had no way to
save at all -- `op_save` existed and nothing ever called it -- and the client's
answer to a dead kernel was to start another one and reopen the file, which is
the document as it was before the session began.

So an edit is written beside the document as it is made, and the tests here
are about the two things that must both be true: the sidecar holds the work,
and it never quietly becomes the document.
"""
import json
import os

import pytest
from conftest import plate

from cadcore.errors import CadError
from cadcore.service.server import Session
from conftest import EXAMPLES
from cadcore.model.document import autosave_path


@pytest.fixture()
def document(tmp_path):
    path = tmp_path / "part.json"
    path.write_text(json.dumps(plate()), encoding="utf-8")
    return str(path)


def test_an_edit_lands_beside_the_document_before_anybody_saves(document):
    session = Session()
    session.op_open(document)
    session.op_build()
    assert session.saved
    assert not os.path.exists(autosave_path(document))

    session.op_set_parameter("t", 13)

    assert not session.saved
    beside = json.loads(open(autosave_path(document), encoding="utf-8").read())
    assert beside["parameters"]["t"] == 13
    assert json.loads(open(document, encoding="utf-8").read())["parameters"]["t"] != 13


def test_a_new_kernel_finds_the_dead_one_s_work_and_says_so(document):
    """Says so, and does not act on it.

    Which of the two the user wants is not something to guess, and a recovery
    nobody was offered is one nobody can decline.
    """
    session = Session()
    session.op_open(document)
    session.op_build()
    session.op_set_parameter("t", 13)      # ... and then the kernel dies

    fresh = Session()
    plain = fresh.op_open(document)
    assert plain["unsaved_work"] == autosave_path(document)
    assert plain["saved"] is True
    assert fresh.doc.parameters["t"] != 13

    recovered = Session()
    out = recovered.op_open(document, recover=True)
    assert out["unsaved_work"] is None
    assert out["saved"] is False                # it is still not in the file
    assert recovered.doc.parameters["t"] == 13
    assert recovered.op_build()["volume_mm3"] > 0
    assert recovered.path == document           # saving puts it where it belongs


def test_saving_puts_the_work_in_the_document_and_drops_the_sidecar(document):
    session = Session()
    session.op_open(document)
    session.op_build()
    session.op_set_parameter("t", 13)

    out = session.op_save()

    assert out["saved"] is True and session.saved
    assert json.loads(open(document, encoding="utf-8").read())["parameters"]["t"] == 13
    assert not os.path.exists(autosave_path(document))


def test_a_stale_sidecar_is_not_offered_as_unsaved_work(document):
    """Newer than the document, not merely present.

    A sidecar left by a session that *did* save is not unsaved work, and
    offering to recover from one would train people to dismiss the only
    message here that ever matters.
    """
    session = Session()
    session.op_open(document)
    session.op_build()
    session.op_set_parameter("t", 13)
    session.op_save()
    open(autosave_path(document), "w", encoding="utf-8").write(json.dumps(plate()))
    os.utime(autosave_path(document), (0, 0))   # older than the document

    assert Session().op_open(document)["unsaved_work"] is None


def test_save_as_moves_the_document_and_leaves_nothing_behind(document, tmp_path):
    session = Session()
    session.op_open(document)
    session.op_build()
    session.op_set_parameter("t", 13)

    elsewhere = str(tmp_path / "copy.json")
    session.op_save(elsewhere)

    assert session.path == elsewhere
    assert not os.path.exists(autosave_path(document))
    assert json.loads(open(elsewhere, encoding="utf-8").read())["parameters"]["t"] == 13
    # and the next edit's sidecar follows the document to its new name
    session.op_set_parameter("t", 14)
    assert os.path.exists(autosave_path(elsewhere))


def test_a_save_with_nothing_open_says_which_of_the_two_is_missing():
    """A document, or a path for it -- refused by name rather than guessed."""
    with pytest.raises(CadError) as exc:
        Session().op_save()
    assert exc.value.kind == "no_document"


def test_work_on_an_imported_step_survives_although_it_was_never_written(tmp_path):
    """The case where losing it costs the most.

    A STEP import has a name for the document it will become and nothing on
    disk under it, so there is nothing to go back to at all. A document that
    is not there counts as older than the sidecar beside it.
    """
    step = tmp_path / "in.step"
    source = Session()
    source.op_open(str(EXAMPLES / "bracket.json"))
    source.op_build()
    source.op_export_step(str(step))

    session = Session()
    session.op_open(str(step))
    session.op_build()
    assert session.path == str(tmp_path / "in.json")            # not written yet
    session.op_add_fillet(
        edges=session.op_select_edges({"of_face": session.body.face_names()[1]})["edges"],
        radius=1.0)                                             # ... and the kernel dies

    recovered = Session()
    out = recovered.op_open(session.path, recover=True)
    assert out["saved"] is False
    assert len(recovered.doc.features) == 2                     # the import and the fillet
    recovered.op_save()
    assert os.path.exists(session.path)
    assert not os.path.exists(autosave_path(session.path))


def test_a_refused_edit_leaves_the_sidecar_alone(document):
    """The sidecar follows the document, and a refusal is not an edit."""
    session = Session()
    session.op_open(document)
    session.op_build()
    session.op_set_parameter("t", 13)
    before = open(autosave_path(document), encoding="utf-8").read()

    with pytest.raises(CadError):
        session.op_set_parameter("no_such_parameter", 1)

    assert open(autosave_path(document), encoding="utf-8").read() == before
