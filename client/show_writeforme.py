import tkinter as tk
import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("-t", "--title", type=str)
parser.add_argument('-b', '--body', type=str)
parser.add_argument('-r', '--reps', type=int)
parser.add_argument('positionals', nargs='*')
args = parser.parse_args()

targettext = args.body
repetitions = args.reps
completedrepetitions = 0
Mistress = tk.Tk()
Mistress.title(args.title)
toptxt = tk.Label(Mistress, text="Write the following for Mommy~")
toptxt.pack(side="top")
targtxt = tk.Label(Mistress, text=targettext, fg="MediumOrchid1")
targtxt.pack(side="top")
usertxt = tk.Entry(Mistress, justify="center")


def check_text(texty):
    global completedrepetitions, repetitions
    if texty == targettext:
        completedrepetitions += 1
        print(completedrepetitions)
        usertxt.delete(0, "end")
        usertxt.after_idle(lambda: usertxt.configure(validate="key"))
        if completedrepetitions == repetitions:
            raise SystemExit
        return True
    if texty == targettext[0:len(texty)]:
        return True
    usertxt.delete(0, "end")
    usertxt.after_idle(lambda: usertxt.configure(validate="key"))
    return False


targetcodevalid = (usertxt.register(check_text), "%P")
usertxt.config(validate="key", validatecommand=targetcodevalid)
Mistress.minsize(300, 50)
usertxt.pack()
Mistress.mainloop()
