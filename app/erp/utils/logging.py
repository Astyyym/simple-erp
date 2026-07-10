import logging
from logging.handlers import RotatingFileHandler

from ..config import project_path


def setup_logging() -> None:
    project_path("logs").mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        app_handler = RotatingFileHandler(project_path("logs", "app.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        app_handler.setFormatter(formatter)
        root.addHandler(app_handler)
        error_handler = RotatingFileHandler(project_path("logs", "error.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root.addHandler(error_handler)
