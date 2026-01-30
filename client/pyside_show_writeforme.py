from PySide6.QtCore import Qt, QRect, Signal, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout, QLineEdit
from PySide6.QtGui import QPixmap, QScreen, QImage, QIcon
import sys
import os


_ACTIVE_DIALOGS: list[QDialog] = []
datafile = "MommyIcon.ico"

if not hasattr(sys, "frozen"):
    datafile = os.path.join(os.path.dirname(__file__), datafile)
else:
    datafile = os.path.join(sys.prefix, datafile)


class WriteForMommy(QDialog):
    completed = Signal()
    
    def __init__(self, text: str, targetreps: int, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Window)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowSystemMenuHint, False)
        self.setStyleSheet("""
            QDialog {
                background-color: #e8d9f1;
            }
            QLabel {
                color: #4b006e;
            }
        """)
        self.completedrepetitions = 0
        self.targetreps = targetreps or 3
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self.validateattempt)
        self._allow_close = False
        self.initUI(text)

    def validateattempt(self):
        texty = self.e1.text()
        if texty == self.targettext:
            self.completedrepetitions += 1
            # print(self.completedrepetitions)
            self.attemptcount.setText(
                f"{self.completedrepetitions}/{self.targetreps}")
            self.e1.clear()
            if self.completedrepetitions >= self.targetreps:
                self._allow_close = True
                print("WFM reached target reps; emitting completed")
                self.completed.emit()
                self.accept()
        if texty == self.targettext[0:len(texty)]:
            pass
        else:
            self.e1.clear()
            
    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
        else:
            event.ignore()
            
    def _on_text_edited(self):
        self._debounce_timer.start(200)  # ms; tweak 150–300

    def initUI(self, text):
        self.targettext = text
        self.my_icon = QIcon()
        self.my_icon.addFile(datafile)
        self.setWindowIcon(self.my_icon)
        self.setWindowTitle("Write For Mommy~")
        self.l1 = QLabel(self, text="Write The Following For Mommy~")
        self.l1.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.l1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ttext = QLabel(self, text=f'"{text}"')
        self.ttext.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.ttext.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.attemptcount = QLabel(
            self, text=f"{self.completedrepetitions}/{self.targetreps}")
        self.attemptcount.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.attemptcount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.e1 = QLineEdit(self)
        self.e1.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.e1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.e1.textEdited.connect(self._on_text_edited)
        self.e1.setStyleSheet("""
            QLineEdit {
                background-color: #ffffff;
                color: #4b006e;
                border: 2px solid #dcc6ea;
                padding: 6px;
                font-size: 14px;
                border-radius: 8px;
            }
            QLineEdit:focus {
                border: 2px solid #4b006e;
            }
        """)
        self.guidetext = QLabel(
            self, text="Enter the text Exactly as shown, cutie~")
        self.guidetext.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.guidetext.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.resize(450, 100)
        layout = QVBoxLayout(self)
        layout.addWidget(self.l1)
        layout.addWidget(self.ttext)
        layout.addWidget(self.attemptcount)
        layout.addWidget(self.e1)
        layout.addWidget(self.guidetext)
        self.show()


def show_wfm(text: str, targetreps: int):
    if text == None or text == "":
        return
    wfm = WriteForMommy(text, targetreps)
    _ACTIVE_DIALOGS.append(wfm)

    def _forget():
        if wfm in _ACTIVE_DIALOGS:
            _ACTIVE_DIALOGS.remove(wfm)

    wfm.destroyed.connect(_forget)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    show_wfm("test text", 5)
    sys.exit(app.exec())
