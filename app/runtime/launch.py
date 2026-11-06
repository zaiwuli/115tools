# ruff: noqa: E402 -- startup measurements intentionally bracket imports
"""Container entry point: select persistent p115 before importing the application."""
import os
import sys

from app.runtime.memory import mark_startup

mark_startup("python_entry")

from app.runtime.config import Config
from app.services.component_manager import active_path


def main():
    mark_startup("launcher_dependencies_loaded")
    selected = active_path(Config(), "p115")
    if selected:
        sys.path.insert(0, selected)
        os.environ["WORK115_RUNNING_P115_PATH"] = selected
    mark_startup("component_selected")
    import uvicorn
    mark_startup("uvicorn_loaded")
    uvicorn.run("app.main:app", host="0.0.0.0", port=5656, workers=1)


if __name__ == "__main__":
    main()
