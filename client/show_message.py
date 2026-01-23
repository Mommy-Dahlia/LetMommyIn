import tkinter as tk
import argparse

def show_message(title: str | None, body: str) -> None:
    if body is None or body == "":
        return

    root = tk.Tk()
    root.title(title or "")
    root.minsize(400, 100)
    
    root.attributes("-topmost", True)   # stay above other windows
    root.lift()                         # raise window
    root.focus_force()                  # try to force focus
    root.after(250, lambda: root.attributes("-topmost", False))  # optional: release topmost


    text = TextPopup(root, body)
    text.pack(expand=True, fill="both")

    root.mainloop()

class TextPopup(tk.Frame):
    def __init__(self, parent, text):
        super().__init__(parent)
        self.parent = parent
        self.text = text
        self.Label = tk.Label(self, text=text)
        self.Label.pack(side="top", expand=True, fill="both")
        self.closeButton = tk.Button(
            self, text="Close", command=self.closepopup)
        self.closeButton.pack(side="bottom")

    def closepopup(self):
        self.parent.destroy()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--title", type=str)
    parser.add_argument("-b", "--body", type=str)
    args = parser.parse_args()

    show_message(args.title, args.body)

