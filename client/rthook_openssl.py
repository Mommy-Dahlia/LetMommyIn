import os
import sys

if sys.platform == "win32" and hasattr(sys, "_MEIPASS"):
    os.environ["SSL_CERT_FILE"] = os.path.join(sys._MEIPASS, "certifi", "cacert.pem")
    os.environ["PATH"] = sys._MEIPASS + os.pathsep + os.environ.get("PATH", "")