"""Read busy times from Calendar through a small helper app.

Homebrew Python has no calendar usage description, so macOS denies EventKit
without a prompt. The helper is its own app and can ask for access. Event
titles are never read. A denial raises CalendarError instead of looking like
an empty calendar.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from goodmoviefinder.calendar_busy import BusyInterval, CalendarError, VIENNA
from goodmoviefinder.config import REPO_ROOT

_SOURCE = Path(__file__).resolve().parent / "CalendarBusy.swift"
_APP = REPO_ROOT / "cache" / "CalendarBusy.app"
_REQUEST = REPO_ROOT / "cache" / "calendar-request.json"
_RESULT = REPO_ROOT / "cache" / "calendar-busy-result.json"
_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>CalendarBusy</string>
  <key>CFBundleIdentifier</key>
  <string>local.goodmoviefinder.calendar</string>
  <key>CFBundleName</key>
  <string>goodmoviefinder</string>
  <key>CFBundleDisplayName</key>
  <string>goodmoviefinder</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSCalendarsUsageDescription</key>
  <string>goodmoviefinder reads when you are busy so a film plan can skip those showtimes.</string>
  <key>NSCalendarsFullAccessUsageDescription</key>
  <string>goodmoviefinder reads when you are busy so a film plan can skip those showtimes.</string>
</dict>
</plist>
"""


def fetch_apple_busy(start: datetime, end: datetime) -> list[BusyInterval]:
    """Timed events that mark you occupied, between start and end."""
    app = _ensure_helper()
    _REQUEST.parent.mkdir(parents=True, exist_ok=True)
    _REQUEST.write_text(
        json.dumps(
            {
                "start": start.timestamp(),
                "end": end.timestamp(),
                "output": str(_RESULT),
            }
        ),
        encoding="utf-8",
    )
    _RESULT.unlink(missing_ok=True)
    print(
        "Asking macOS for calendar access. Allow goodmoviefinder if a prompt appears.",
        file=sys.stderr,
    )
    try:
        subprocess.run(
            ["open", "-W", "-n", str(app)],
            check=False,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise CalendarError("Timed out waiting for Calendar access.") from exc
    if not _RESULT.is_file():
        raise CalendarError(
            "Calendar access did not finish. Allow goodmoviefinder in "
            "System Settings → Privacy & Security → Calendars, then run the command again."
        )
    try:
        payload = json.loads(_RESULT.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CalendarError("Calendar access returned an unreadable result.") from exc
    error = payload.get("error") if isinstance(payload, dict) else None
    if error:
        raise CalendarError(str(error))
    intervals: list[BusyInterval] = []
    for item in payload.get("intervals") or []:
        parsed = _interval(item)
        if parsed is not None:
            intervals.append(parsed)
    return intervals


def _interval(item: object) -> BusyInterval | None:
    if not isinstance(item, dict):
        return None
    start_raw = item.get("start")
    end_raw = item.get("end")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        return None
    start = _parse_datetime(start_raw)
    end = _parse_datetime(end_raw)
    if end <= start:
        return None
    return BusyInterval(start, end)


def _parse_datetime(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=VIENNA)
    return parsed


def _ensure_helper() -> Path:
    executable = _APP / "Contents" / "MacOS" / "CalendarBusy"
    if executable.is_file() and executable.stat().st_mtime >= _SOURCE.stat().st_mtime:
        return _APP
    if not _SOURCE.is_file():
        raise CalendarError(f"Missing calendar helper source at {_SOURCE}.")
    macos = executable.parent
    macos.mkdir(parents=True, exist_ok=True)
    (_APP / "Contents" / "Info.plist").write_text(_PLIST, encoding="utf-8")
    print("Building the calendar helper…", file=sys.stderr)
    compiled = subprocess.run(
        ["swiftc", "-O", "-framework", "EventKit", "-o", str(executable), str(_SOURCE)],
        capture_output=True,
        text=True,
    )
    if compiled.returncode != 0:
        detail = (compiled.stderr or compiled.stdout or "").strip()
        raise CalendarError(
            "Could not build the calendar helper. Install the Xcode command line "
            f"tools with `xcode-select --install` and run the command again. {detail}"
        )
    signed = subprocess.run(
        ["codesign", "--force", "--sign", "-", str(_APP)],
        capture_output=True,
        text=True,
    )
    if signed.returncode != 0:
        detail = (signed.stderr or signed.stdout or "").strip()
        raise CalendarError(f"Could not sign the calendar helper. {detail}")
    return _APP
