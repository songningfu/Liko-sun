"""Local prototype entry point. Production continues to use app.main:app."""

from pathlib import Path

from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .main import app


ui = Path(__file__).resolve().parents[1] / "pocket-ui"
app.mount("/pocket", StaticFiles(directory=ui, html=True), name="pocket-ui")


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/pocket/")
