import tkinter as tk
from tkinter import ttk
from PIL import Image
from PIL import ImageTk
import argparse
import requests
import sys
from io import BytesIO


class ImagePopup(tk.Frame):
    def __init__(self, parent, imageurl):
        super().__init__(parent)
        self.parent = parent
        self.parent.overrideredirect(True)
        self.imdata = requests.get(imageurl)  # get the image from the web
        self.max = 400
        if self.imdata.status_code == 200:  # if the image was got successfully
            # make the web image into a PIL image
            self.imaged = Image.open(BytesIO(self.imdata.content))
            alpha = self.imaged.getchannel('A')  # get the alpha channel
            bbox = alpha.getbbox()
            # crop as much as possible that's just transparency
            self.cropim = self.imaged.crop(bbox)
            self.cropim.thumbnail((self.max, self.max),
                                  Image.Resampling.LANCZOS)  # take the result and scale it to fit within self.max
            self.actualim = ImageTk.PhotoImage(
                image=self.cropim)  # make it work in tkinter
            self.image = tk.Label(self, image=self.actualim)  # display it

        match sys.platform:  # stuff for making the transparency work properly
            case "linux" | "linux2":
                self.parent.wait_visibility(self.parent)
                self.parent.wm_attributes("-alpha", 0.5)
            case "darwin":
                self.parent.wm_attributes("-transparent", True)
            case "win32":
                self.parent.wm_attributes("-transparentcolor", "white")
                self.image.config(bg="White")
        if self.imdata.status_code == 200:
            self.image.pack()
        else:
            raise SystemExit
        self.parent.bind("<Button-1>", self.closepopup)
        self.parent.bind("<ButtonPress-3>", self.start_move)
        self.parent.bind('<B3-Motion>', self.move_window)

    def closepopup(self, event):
        self.parent.destroy()

    def start_move(self, event):
        self.lastx = event.x_root
        self.lasty = event.y_root

    def move_window(self, event):
        deltax = event.x_root - self.lastx
        deltay = event.y_root - self.lasty
        self.x = self.parent.winfo_x() + deltax
        self.y = self.parent.winfo_y() + deltay
        self.parent.geometry("+%s+%s" % (self.x, self.y))
        self.lastx = event.x_root
        self.lasty = event.y_root


parser = argparse.ArgumentParser()
parser.add_argument('-u', '--url', type=str,
                    default="http://www.republiquedesmangues.fr/mangue.png")
args = parser.parse_args()

if args.url != None and args.url != "":
    Mistress = tk.Tk()
    text = ImagePopup(Mistress, args.url)
    text.pack(expand=True, fill="both")
    Mistress.mainloop()
