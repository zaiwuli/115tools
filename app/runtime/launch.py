"""Container entry point: select persistent p115 before importing the application."""
import os
import sys

from app.runtime.config import Config
from app.services.component_manager import active_path


def main():
    selected = active_path(Config(), "p115")
    if selected:
        sys.path.insert(0, selected)
        os.environ["WORK115_RUNNING_P115_PATH"] = selected
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=5656, workers=1)


if __name__ == "__main__":
    main()
