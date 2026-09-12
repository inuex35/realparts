"""The one error type the core raises, and the registry of every kind it raises."""
from __future__ import annotations


#: Every kind this codebase raises, grouped by where the refusal comes from
#: rather than alphabetically.
KINDS = frozenset({
    # the document, before any geometry
    "empty_document", "unknown_format", "unknown_unit", "unknown_parameter",
    "parameter_unit_unknown", "bad_parameter", "bad_expression",
    "unsafe_expression", "expression_too_big", "no_document", "no_path",
    "file_not_found", "bad_path",

    # the transport: answered as refusal replies (bad_json is also raised, by
    # Document.load, for a file that is not JSON)
    "bad_json", "unknown_op", "internal_error", "bridge_closed", "not_admitted",

    # the graph: what a feature is and what it may name
    "unknown_feature", "unknown_feature_type", "unknown_argument",
    "missing_argument", "bad_arguments", "cyclic_graph", "feature_in_use",
    "not_a_chain_feature", "cannot_suppress", "nothing_to_undo",
    # the search
    "nothing_to_optimise", "no_feasible_design",
    "nothing_to_redo", "no_solid", "nothing_builds", "not_built",
    "outside_envelope",

    # references into geometry
    "unresolved_reference", "unknown_reference", "unknown_face", "bad_face",
    "empty_selection", "bad_query", "face_was_split", "unknown_curve",
    "unknown_point", "unknown_line", "unknown_axis", "unknown_plane",
    "unknown_datum", "unknown_sketch", "not_a_sketch", "degenerate_frame",
    "no_sketch_behind_it",

    # what the kernel would not build
    "boolean_failed", "fillet_failed", "chamfer_failed", "shell_failed",
    # OCCT segfaults on some fillets rather than refusing them; these mark an
    # operation the sentry process crashed on, so this process never attempted
    # it. See cadcore/geometry/solids/sentry.py
    "fillet_crashed", "chamfer_crashed",
    "draft_failed", "extrude_failed", "revolve_failed", "loft_failed",
    "sweep_failed", "rib_failed", "thread_failed", "flange_failed",
    "offset_failed", "thicken_failed", "trim_failed", "extend_failed", "split_failed",
    "fill_failed", "skin_failed", "surface_failed", "defeature_failed",
    "move_failed", "export_failed", "invalid_shape", "empty_result",
    # a study asked for on a checkout without requirements-sim.txt
    "missing_dependency",
    "empty_mesh", "no_intersection", "nothing_to_fill", "nothing_to_stop_at",
    # a part document that brings itself in, directly or through another
    "circular_part",
    # mechanisms (`mechanism`): links and gear trains that will not assemble
    "not_a_mechanism", "jammed", "planets_will_not_space", "planets_touch",
    "cannot_assemble", "too_few_slots",
    "trim_empty", "degenerate_segment", "unknown_end_condition",

    # the shape is not the shape this operation needs
    "not_a_solid", "not_a_cylinder", "not_planar", "non_planar_face",
    "empty_sketch", "open_profile", "bad_profile", "already_closed",

    # sketches
    "sketch_unsolved", "sketch_conflicting", "sketch_underconstrained",
    "bad_constraint", "unknown_constraint",

    # sheet metal
    "not_a_sheet", "not_sheet_metal", "not_a_sheet_edge", "no_flat_pattern",

    # assemblies
    "not_an_assembly", "unknown_mate", "over_constrained", "bad_mate_faces",
    # a drive asks a part to move a way its mates do not allow
    "no_freedom",
    # a study of an assembly glues the parts; one that touches nothing cannot be
    "parts_not_touching",

    # drawings
    "no_drawing", "unknown_view", "not_in_view", "cannot_project",
    "empty_view", "empty_section", "not_a_dimension", "unknown_dimension",
    "unknown_tolerance", "view_not_on_the_sheet",

    # exchange
    "step_read_failed", "iges_read_failed", "empty_step", "empty_iges",
    "mesh_read_failed", "brep_read_failed",

    # fasteners
    "unknown_fastener",

    # the studies (`simulation`)
    "unknown_study_type", "unknown_quantity", "unknown_material",
    "bad_requirement", "unknown_requirement", "duplicate_id", "no_assembly",
    # the solver returned a non-answer: a negative mode, an unconverged field
    "study_failed",
    # a study with nothing applied: every result is zero and any requirement
    # would pass
    "nothing_to_solve",
    "no_material_property", "study_underconstrained",
    "mesh_face_mismatch", "boundary_not_in_mesh", "no_buckling_mode",
})


def short(value, limit: int = 80) -> object:
    """A value small enough to put in a refusal's detail.

    The value itself when its repr fits in ``limit`` characters, so callers get
    the number or name back; the truncated repr otherwise.
    """
    text = repr(value)
    return value if len(text) <= limit else text[:limit - 3] + "..."


class CadError(Exception):
    """Failure with a machine-readable kind, so callers can react to it."""

    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.detail = detail or {}
