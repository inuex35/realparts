# bench

A benchmark: the same plain-English part description is given to an
assistant twice, once with only the RealParts MCP server and once with a
text-to-CAD code-generation tool, and the STEP file each produces is scored
by the same checks.

Each spec in `specs/` is a description of a part plus a list of checks the
kernel can answer about the result: one solid, size, mass, number of holes
of a given size, printability, strength. `run.py` runs both arms with the
same caps on turns and cost; `check.py` scores the STEP without knowing
which arm produced it.

    PYTHONPATH=. .venv/bin/python bench/run.py --arm cadcore   --spec l_bracket
    TEXTTOCAD_PYTHON=/path/to/python-with-cadgen \
    PYTHONPATH=. .venv/bin/python bench/run.py --arm texttocad --spec l_bracket

Results land under `results/<arm>/<spec>/`: the transcript, the working
directory with the part, and `score.json`.

Rules that keep the comparison fair:

* the prompt is identical apart from one sentence naming the tool and the
  output path;
* checks are about the part, never about how it was made. Bounding boxes are
  compared as sorted extents, so orientation does not matter;
* the FEM check stands the part on its lowest flat face and loads its
  highest, found by geometry rather than by either tool's face names;
* a run that produces no file scores zero, and the score says why;
* results are reported for both arms whatever they show. The purpose is to
  find where RealParts loses and fix it.
