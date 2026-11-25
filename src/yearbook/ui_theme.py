"""Consistent readable app colors, independent of the Windows dark palette."""

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette


def apply_theme(app):
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    palette = QPalette()
    colors = {
        "Window": "#f5f5f3", "WindowText": "#24303a", "Base": "#ffffff",
        "AlternateBase": "#f4f5f6", "Text": "#24303a", "Button": "#ffffff",
        "ButtonText": "#24303a", "ToolTipBase": "#ffffff", "ToolTipText": "#24303a",
        "Highlight": "#7f2639", "HighlightedText": "#ffffff", "Link": "#7f2639",
        "LinkVisited": "#7f2639", "PlaceholderText": "#5f6b76", "Light": "#ffffff",
        "Midlight": "#eef0f1", "Mid": "#c9ced3", "Dark": "#6a747d", "Shadow": "#24303a",
    }
    for role, value in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    for role in ("WindowText", "Text", "ButtonText", "PlaceholderText"):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor("#616b75"))
    app.setPalette(palette)
    if not app.property("yearbookFontsLoaded"):
        directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "tahoma.ttf"):
            font = directory / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        app.setProperty("yearbookFontsLoaded", True)
    app.setFont(QFont("Segoe UI", 10))


STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #24303a; }
QMainWindow, QDialog { background: #f5f5f3; }
QLabel#eyebrow { color: #7f2639; font-size: 9pt; font-weight: 700; }
QLabel#heading { font-size: 18pt; font-weight: 650; }
QLabel#banner { background: #edf0f2; color: #435463; padding: 7px 10px; border-radius: 4px; }
QLabel#muted { color: #596773; font-size: 9pt; }
QLabel#studentCard { color: #24303a; background: white; border: 1px solid #d6dadd; border-radius: 6px; padding: 12px; }
QLabel#activeCard { color: #24303a; font-size: 12pt; font-weight: 600; padding: 4px 0; }
QLabel#nextStep { color: #445665; background: #eef2f4; padding: 9px; border-radius: 4px; }
QLabel#validationError { color: #8b2038; padding: 4px; }
QFrame#welcomeCard { background: white; border: 1px solid #d6dadd; border-radius: 6px; }
QLineEdit { background: white; color: #24303a; border: 1px solid #b7bfc6; border-radius: 4px; padding: 8px; selection-background-color: #7f2639; selection-color: white; }
QLineEdit:focus { border: 2px solid #7f2639; }
QLineEdit:disabled { background: #eef0f2; color: #616b75; }
QPushButton { background: #ffffff; color: #24303a; border: 1px solid #bcc4cb; border-radius: 4px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #eef0f2; border-color: #8795a1; }
QPushButton:focus { border: 2px solid #7f2639; }
QPushButton[primary="true"] { background: #7f2639; color: white; border-color: #7f2639; }
QPushButton[primary="true"]:hover { background: #962d44; }
QPushButton:disabled { background: #e9ecee; color: #616b75; border-color: #d0d5da; }
QCheckBox { color: #24303a; padding: 4px 0; }
QCheckBox:disabled { color: #616b75; }
QTabWidget::pane { background: white; border: 1px solid #d6dadd; border-radius: 4px; }
QTabBar::tab { background: #eceff1; color: #435463; padding: 8px 16px; border: none; }
QTabBar::tab:selected { background: white; color: #7f2639; border-bottom: 3px solid #7f2639; font-weight: 700; }
QTableWidget, QListWidget, QTextEdit, QTextBrowser { background: white; color: #24303a; alternate-background-color: #f4f5f6; border: 1px solid #d6dadd; border-radius: 4px; selection-background-color: #e7d9df; selection-color: #24303a; }
QHeaderView::section { background: #eef0f2; color: #24303a; border: none; padding: 8px; font-weight: 600; }
QGroupBox { background: white; color: #24303a; border: 1px solid #d6dadd; border-radius: 4px; margin-top: 10px; padding: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QScrollArea { border: none; background: white; }
QStatusBar { color: #435463; font-size: 9pt; }
"""
