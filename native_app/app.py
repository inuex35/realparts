"""Start the desktop app."""
from __future__ import annotations

import sys


def main(argv: list | None = None) -> int:
    try:
        from PySide6.QtGui import QSurfaceFormat
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("the desktop app needs PySide6: pip install -r requirements-native.txt", file=sys.stderr)
        return 2
    surface = QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QSurfaceFormat.CoreProfile)
    surface.setDepthBufferSize(24)
    surface.setSamples(4)
    QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication(argv if argv is not None else sys.argv)
    from .window import Window

    window = Window()
    for arg in app.arguments()[1:]:
        if not arg.startswith("-"):
            window.open(arg)
    window.show()
    return app.exec()
