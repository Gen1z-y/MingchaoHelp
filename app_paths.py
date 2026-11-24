from pathlib import Path
import sys


APP_DIRECTORY = (Path(sys.executable) if getattr(sys, "frozen", False)
                 else Path(__file__)).resolve().parent
LOCAL_DATA = APP_DIRECTORY / ".local"
