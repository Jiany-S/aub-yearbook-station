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
        "Window": "#ffffff", "WindowText": "#292425", "Base": "#ffffff",
        "AlternateBase": "#f8f6f4", "Text": "#292425", "Button": "#ffffff",
        "ButtonText": "#292425", "ToolTipBase": "#ffffff", "ToolTipText": "#292425",
        "Highlight": "#650d1b", "HighlightedText": "#ffffff", "Link": "#650d1b",
        "LinkVisited": "#650d1b", "PlaceholderText": "#70696b", "Light": "#ffffff",
        "Midlight": "#f3f0ed", "Mid": "#d8d2cf", "Dark": "#70696b", "Shadow": "#292425",
    }
    for role, value in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    for role in ("WindowText", "Text", "ButtonText", "PlaceholderText"):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor("#70696b"))
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
QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #292425; }
QMainWindow, QDialog, QWidget#appRoot { background: #ffffff; }
QFrame#masthead { background: #ffffff; }
QLabel#eyebrow { color: #650d1b; font-size: 9pt; font-weight: 600; }
QLabel#heading { color: #292425; font-size: 18pt; font-weight: 600; }
QLabel#brandCaption { color: #70696b; font-size: 9pt; }
QLabel#banner { background: #f8f6f4; color: #534b4e; padding: 9px 12px; border-left: 3px solid #d8d2cf; }
QLabel#banner[active="true"] { border-left-color: #650d1b; background: #faf5f5; color: #650d1b; }
QLabel#muted, QLabel#cameraSummary { color: #70696b; font-size: 9pt; }
QLabel#studentCard { color: #292425; background: #ffffff; border: none; padding: 12px 0; }
QLabel#activeCard { color: #292425; font-size: 15pt; font-weight: 600; padding: 5px 0; }
QLabel#nextStep { color: #655d60; padding: 6px 0; }
QLabel#validationError { color: #8b2038; padding: 4px 0; }
QFrame#studentPanel { background: #ffffff; border-right: 1px solid #e7e1df; }
QFrame#welcomeCard { background: #ffffff; border: none; }
QFrame#demoPanel { background: #faf7f5; border: 1px solid #e7e1df; }
QLineEdit { background: #ffffff; color: #292425; border: 1px solid #bfb6b5; border-radius: 3px; padding: 10px; selection-background-color: #650d1b; selection-color: #ffffff; }
QLineEdit:focus { border: 2px solid #650d1b; }
QLineEdit:disabled { background: #f4f1ef; color: #70696b; border-color: #e4deda; }
QPushButton { background: #ffffff; color: #292425; border: 1px solid #cfc5c3; border-radius: 3px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #f8f4f1; border-color: #a79a98; }
QPushButton:focus { border: 2px solid #650d1b; }
QPushButton[primary="true"] { background: #650d1b; color: #ffffff; border-color: #650d1b; }
QPushButton[primary="true"]:hover { background: #80172b; border-color: #80172b; }
QPushButton:disabled { background: #f1eeec; color: #70696b; border-color: #e0d9d6; }
QPushButton#cameraDemo, QPushButton#openStudentFolder { background: transparent; border-color: transparent; color: #650d1b; }
QPushButton#cameraDemo:hover, QPushButton#openStudentFolder:hover { background: #f7f0f0; }
QPushButton#startSession, QPushButton#finishSession { padding: 12px 18px; }
QCheckBox { color: #292425; padding: 8px 0; spacing: 8px; }
QCheckBox:disabled { color: #70696b; }
QTabWidget::pane { background: #ffffff; border: none; border-top: 1px solid #e7e1df; }
QTabBar::tab { background: #ffffff; color: #70696b; padding: 12px 20px; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: #650d1b; background: #faf7f5; }
QTabBar::tab:selected { color: #650d1b; border-bottom: 2px solid #650d1b; font-weight: 600; }
QTableWidget, QListWidget, QTextEdit, QTextBrowser { background: #ffffff; color: #292425; alternate-background-color: #faf8f6; border: 1px solid #e7e1df; selection-background-color: #f1e4e6; selection-color: #292425; }
QListWidget#photoGrid { background: #faf9f7; border: none; padding: 8px; }
QListWidget#photoGrid::item { padding: 8px; border: 1px solid transparent; }
QListWidget#photoGrid::item:selected { background: #f1e4e6; border-color: #b58e97; }
QHeaderView::section { background: #f7f4f1; color: #534b4e; border: none; border-bottom: 1px solid #e7e1df; padding: 10px; font-weight: 600; }
QGroupBox { background: #ffffff; color: #292425; border: 1px solid #e7e1df; margin-top: 12px; padding: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QScrollArea { border: none; background: #ffffff; }
QStatusBar { color: #70696b; background: #faf8f6; border-top: 1px solid #e7e1df; font-size: 9pt; }
"""
