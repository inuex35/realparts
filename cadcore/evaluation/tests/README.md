# cadcore/evaluation/tests

`golden.json` records what every shipped example measures. `test_golden.py`
compares against it; when a geometry change moves a number or a face name
on purpose, check the new value is right and run

    python cadcore/evaluation/tests/golden.py --write

then say in the commit what changed and why.
