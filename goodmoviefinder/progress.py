"""Live build progress for the local preview page."""

from __future__ import annotations

import html
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_ASSET_DIR = Path(__file__).resolve().parent

STEP_DEFS: tuple[tuple[str, str], ...] = (
    ("program", "Nonstop program"),
    ("profile", "Letterboxd profile"),
    ("films", "Match films on Letterboxd"),
    ("credits", "Directors of films you loved"),
    ("streaming", "Streaming in Austria"),
    ("calendar", "Calendar busy times"),
    ("recommendations", "Recommendations"),
    ("pages", "Write the pages"),
)

_tracker: BuildProgress | None = None
_active_step: ContextVar[str | None] = ContextVar("progress_step", default=None)


class BuildProgress:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.phase = "starting"
        self.program_ready = False
        self.error: str | None = None
        self.steps: dict[str, dict[str, object]] = {
            step_id: {
                "id": step_id,
                "label": label,
                "status": "pending",
                "current": 0,
                "total": None,
                "detail": "",
            }
            for step_id, label in STEP_DEFS
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            steps = [dict(step) for step in self.steps.values()]
            return {
                "phase": self.phase,
                "programReady": self.program_ready,
                "error": self.error,
                "overall": _overall(steps),
                "steps": steps,
            }

    def start(self, step_id: str, *, total: int | None = None, detail: str = "") -> None:
        with self._lock:
            self.phase = "running"
            step = self.steps[step_id]
            step["status"] = "running"
            step["current"] = 0
            if total is not None:
                step["total"] = total
            if detail:
                step["detail"] = detail

    def update(
        self,
        step_id: str,
        *,
        current: int | None = None,
        total: int | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            step = self.steps[step_id]
            if step["status"] == "pending":
                step["status"] = "running"
                self.phase = "running"
            if current is not None:
                step["current"] = current
            if total is not None:
                step["total"] = total
            if detail is not None:
                step["detail"] = detail

    def finish(self, step_id: str, *, detail: str = "") -> None:
        with self._lock:
            step = self.steps[step_id]
            step["status"] = "done"
            if step["total"] is not None:
                step["current"] = step["total"]
            if detail:
                step["detail"] = detail
            elif not step["detail"]:
                step["detail"] = "Done"

    def skip(self, step_id: str, detail: str = "Skipped") -> None:
        with self._lock:
            step = self.steps[step_id]
            step["status"] = "skipped"
            step["detail"] = detail

    def fail(self, message: str, step_id: str | None = None) -> None:
        with self._lock:
            self.phase = "error"
            self.error = message
            if step_id is not None:
                step = self.steps[step_id]
                step["status"] = "error"
                step["detail"] = message

    def mark_program(self) -> None:
        with self._lock:
            self.program_ready = True

    def ready(self) -> None:
        with self._lock:
            self.phase = "ready"
            self.program_ready = True
            self.error = None


def bind(tracker: BuildProgress | None) -> None:
    global _tracker
    _tracker = tracker


def get() -> BuildProgress | None:
    return _tracker


@contextmanager
def step(step_id: str, *, total: int | None = None, detail: str = ""):
    tracker = _tracker
    token = _active_step.set(step_id)
    if tracker is not None:
        tracker.start(step_id, total=total, detail=detail)
    try:
        yield tracker
    except Exception as exc:
        if tracker is not None:
            tracker.fail(str(exc), step_id)
        raise
    else:
        if tracker is not None and tracker.steps[step_id]["status"] == "running":
            tracker.finish(step_id)
    finally:
        _active_step.reset(token)


def skip(step_id: str, detail: str = "Skipped") -> None:
    if _tracker is not None:
        _tracker.skip(step_id, detail)


def tick(
    *,
    current: int | None = None,
    total: int | None = None,
    detail: str | None = None,
) -> None:
    tracker = _tracker
    step_id = _active_step.get()
    if tracker is None or not step_id:
        return
    tracker.update(step_id, current=current, total=total, detail=detail)


def _overall(steps: list[dict[str, object]]) -> float:
    if not steps:
        return 0.0
    total = 0.0
    for step in steps:
        status = step["status"]
        if status in {"done", "skipped"}:
            total += 1.0
        elif status in {"running", "error"}:
            current = step["current"]
            count = step["total"]
            if isinstance(current, int) and isinstance(count, int) and count > 0:
                total += min(1.0, current / count)
            else:
                total += 0.15
    return min(1.0, total / len(steps))


def format_progress_html() -> str:
    css = (_ASSET_DIR / "base.css").read_text(encoding="utf-8")
    css += (_ASSET_DIR / "progress.css").read_text(encoding="utf-8")
    js = (_ASSET_DIR / "progress.js").read_text(encoding="utf-8")
    rows = "".join(
        '<li class="build-step is-pending" data-step="'
        + html.escape(step_id)
        + '">'
        '<div class="build-step-head">'
        '<span class="build-step-mark" aria-hidden="true"></span>'
        f'<span class="build-step-label">{html.escape(label)}</span>'
        '<span class="build-step-count"></span>'
        "</div>"
        '<div class="build-bar"><span></span></div>'
        '<p class="build-step-detail"></p>'
        "</li>"
        for step_id, label in STEP_DEFS
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Building — Nonstop × Letterboxd</title>
  <style>
{css}  </style>
</head>
<body class="page-build">
  <header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="/">Nonstop <b>&times;</b> Letterboxd</a>
    </div>
  </header>
  <section class="page-head">
    <h1>Building the pages</h1>
    <p class="tagline" id="build-tagline">Fetching the Nonstop program and Letterboxd data.</p>
    <p class="build-overall-label" id="build-overall-label">0%</p>
    <div class="build-bar build-bar-overall" id="build-overall"><span></span></div>
    <p class="build-error" id="build-error" hidden></p>
  </section>
  <main>
    <ol class="build-steps panel" id="build-steps">{rows}</ol>
  </main>
  <script>
{js}  </script>
</body>
</html>
"""
