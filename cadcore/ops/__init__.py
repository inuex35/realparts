"""The session's operations, grouped by what they are for.

Each module is a mixin: one chapter of the editing API, working on the session's
own state. They are gathered into :class:`cadcore.ops.session.Session`, which is
what the protocol and the tests see -- so this split costs nothing at the
boundary and buys a file per subject.
"""
