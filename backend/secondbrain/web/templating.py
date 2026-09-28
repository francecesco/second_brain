"""Jinja: template, filtri di formato, nomi dei mesi."""
from pathlib import Path

from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
          "agosto", "settembre", "ottobre", "novembre", "dicembre")
KIB = 1024
MIB = 1024 * 1024


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def format_size(size: int) -> str:
    if size < MIB:
        return f"{max(1, round(size / KIB))} KB"
    return f"{size / MIB:.1f} MB".replace(".", ",")


templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.filters["duration"] = format_duration
templates.env.filters["size"] = format_size
templates.env.globals["MONTHS"] = MONTHS
