# rthook_qt_env.py
import os
import sys

if hasattr(sys, "_MEIPASS"):
    base = sys._MEIPASS

    # Let Windows loader find Qt6*.dll / shiboken DLLs in the extracted dir
    os.environ["PATH"] = base + os.pathsep + os.environ.get("PATH", "")

    # Point Qt at the extracted plugins folder
    pyside = os.path.join(base, "PySide6")
    plugins = os.path.join(pyside, "plugins")
    os.environ["QT_PLUGIN_PATH"] = plugins

    # If you ever use QML later, this helps too (harmless if unused)
    qml = os.path.join(pyside, "qml")
    os.environ.setdefault("QML2_IMPORT_PATH", qml)
