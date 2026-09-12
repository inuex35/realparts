"""The look: one dark, flat stylesheet for the whole window."""

ACCENT = "#7aa2f7"

QSS = """
QMainWindow, QDockWidget, QWidget { background: #14171e; color: #e9ecf1;
  font-family: Inter, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 13px; }
QToolBar { background: #1a1e28; border: 0; border-bottom: 1px solid #262b36; padding: 6px 10px; spacing: 6px; }
QToolBar QToolButton { background: transparent; border: 0; border-radius: 8px; padding: 6px 10px; color: #e9ecf1; }
QToolBar QToolButton:hover { background: rgba(255,255,255,0.08); }
QToolBar QComboBox { min-width: 220px; }
QDockWidget { border: 0; }
QTabWidget::pane { border: 0; background: #1a1e28; border-radius: 12px; margin: 10px; padding: 6px; }
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab { background: transparent; color: #8b93a3; padding: 8px 14px; margin: 10px 2px 0 2px; border-radius: 8px; }
QTabBar::tab:hover { color: #e9ecf1; background: rgba(255,255,255,0.05); }
QTabBar::tab:selected { color: #e9ecf1; background: rgba(255,255,255,0.10); }
QPushButton { background: rgba(255,255,255,0.06); border: 1px solid transparent; border-radius: 9px; padding: 6px 12px; }
QPushButton:hover { background: rgba(255,255,255,0.10); }
QPushButton:disabled { color: #5b6270; }
QPushButton#primary { background: %(accent)s; color: #0b1020; font-weight: 600; }
QLineEdit, QTextEdit, QTextBrowser, QComboBox, QDoubleSpinBox, QListWidget, QTableWidget {
  background: rgba(255,255,255,0.05); border: 1px solid transparent; border-radius: 9px; padding: 5px 8px;
  selection-background-color: %(accent)s; selection-color: #0b1020; }
QLineEdit:focus, QTextEdit:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: %(accent)s; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: #1f2430; border: 1px solid #2c3240; selection-background-color: rgba(122,162,247,0.25); }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0; }
QListWidget::item { padding: 6px 8px; border-radius: 8px; margin: 1px 0; }
QListWidget::item:selected { background: rgba(122,162,247,0.18); color: #e9ecf1; }
QTableWidget { gridline-color: #262b36; }
QHeaderView::section { background: transparent; color: #8b93a3; border: 0; padding: 4px; font-size: 11px; text-transform: uppercase; }
QTableWidget::item { padding: 4px; }
QLabel { color: #8b93a3; }
QLabel#pick { color: #8b93a3; padding: 10px 14px; border-bottom: 1px solid #262b36; }
QLabel#pick[on="true"] { color: #e9ecf1; border-left: 3px solid #7aa2f7; }
QLabel#docname { color: #8b93a3; padding: 0 10px; }
QToolButton#section { background: transparent; border: 0; color: #8b93a3; font-size: 11px; font-weight: 600;
  letter-spacing: 1px; padding: 10px 12px; text-transform: uppercase; }
QToolButton#section:hover { color: #e9ecf1; }
QWidget#numbers { background: rgba(242,193,78,0.08); border: 1px solid rgba(242,193,78,0.25); border-radius: 10px; }
QScrollArea { background: transparent; border: 0; }
QLabel#title { color: #8b93a3; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.15); border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: %(accent)s; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QStatusBar { background: #1a1e28; color: #8b93a3; border-top: 1px solid #262b36; }
QMenu { background: #1f2430; border: 1px solid #2c3240; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 14px; border-radius: 7px; }
QMenu::item:selected { background: rgba(122,162,247,0.2); }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.14); border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QCheckBox::indicator { width: 14px; height: 14px; border-radius: 4px; border: 1px solid #3a4150; background: transparent; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }
""" % {"accent": ACCENT}
