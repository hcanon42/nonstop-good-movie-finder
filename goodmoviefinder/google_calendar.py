"""Read busy times from Google Calendar with a desktop OAuth login."""

from __future__ import annotations

import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from goodmoviefinder.calendar_busy import BusyInterval, CalendarError, VIENNA
from goodmoviefinder.config import GOOGLE_CLIENT_PATH, GOOGLE_TOKEN_PATH

_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_FIELDS = "items(start,end,transparency,status,attendees(self,responseStatus)),nextPageToken"


class _LoginExpired(CalendarError):
    pass


def fetch_google_busy(start: datetime, end: datetime) -> list[BusyInterval]:
    """Timed events that mark the account occupied, between start and end."""
    access = _access_token()
    try:
        return _busy_intervals(access, start, end)
    except _LoginExpired:
        access = _interactive_login()
        return _busy_intervals(access, start, end)


def _access_token() -> str:
    token = _load_token()
    if token is None:
        return _interactive_login()
    if not _expired(token):
        access = token.get("access_token")
        if isinstance(access, str) and access:
            return access
    refresh = token.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        try:
            return _refresh(token)
        except CalendarError:
            pass
    return _interactive_login()


def _busy_intervals(access: str, start: datetime, end: datetime) -> list[BusyInterval]:
    intervals: list[BusyInterval] = []
    for calendar_id in _selected_calendar_ids(access):
        intervals.extend(_calendar_events(access, calendar_id, start, end))
    return intervals


def _selected_calendar_ids(access: str) -> list[str]:
    ids: list[str] = []
    page_token = ""
    for _ in range(20):
        query: dict[str, str] = {"fields": "items(id,selected),nextPageToken"}
        if page_token:
            query["pageToken"] = page_token
        payload = _api_get(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList?"
            + urllib.parse.urlencode(query),
            access,
        )
        for item in payload.get("items") or []:
            if item.get("selected") and isinstance(item.get("id"), str):
                ids.append(item["id"])
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break
    return ids or ["primary"]


def _calendar_events(
    access: str,
    calendar_id: str,
    start: datetime,
    end: datetime,
) -> list[BusyInterval]:
    intervals: list[BusyInterval] = []
    page_token = ""
    encoded = urllib.parse.quote(calendar_id, safe="")
    for _ in range(50):
        query = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": _rfc3339(start),
            "timeMax": _rfc3339(end),
            "maxResults": "250",
            "fields": _FIELDS,
        }
        if page_token:
            query["pageToken"] = page_token
        payload = _api_get(
            f"https://www.googleapis.com/calendar/v3/calendars/{encoded}/events?"
            + urllib.parse.urlencode(query),
            access,
        )
        for item in payload.get("items") or []:
            interval = _interval_from_event(item)
            if interval is not None:
                intervals.append(interval)
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break
    return intervals


def _interval_from_event(item: dict) -> BusyInterval | None:
    if item.get("status") == "cancelled":
        return None
    if item.get("transparency") == "transparent":
        return None
    if _declined(item):
        return None
    start_info = item.get("start") or {}
    end_info = item.get("end") or {}
    start_raw = start_info.get("dateTime")
    end_raw = end_info.get("dateTime")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        return None
    start = _parse_datetime(start_raw)
    end = _parse_datetime(end_raw)
    if end <= start:
        return None
    return BusyInterval(start, end)


def _declined(item: dict) -> bool:
    for attendee in item.get("attendees") or []:
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return True
    return False


def _parse_datetime(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=VIENNA)
    return parsed


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(VIENNA).isoformat()


def _load_client() -> tuple[str, str, str, str]:
    path = GOOGLE_CLIENT_PATH
    if not path.is_file():
        raise CalendarError(
            "Save a Desktop OAuth client JSON at "
            f"{path}. Create it in Google Cloud after enabling the Calendar API."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalendarError(f"{path} is not a Google OAuth client file.") from exc
    block = raw.get("installed") or raw.get("web") or {}
    client_id = block.get("client_id")
    client_secret = block.get("client_secret")
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise CalendarError(f"{path} is not a Google OAuth client file.")
    auth_uri = block.get("auth_uri") if isinstance(block.get("auth_uri"), str) else _AUTH_URI
    token_uri = block.get("token_uri") if isinstance(block.get("token_uri"), str) else _TOKEN_URI
    return client_id, client_secret, auth_uri, token_uri


def _load_token() -> dict | None:
    path = GOOGLE_TOKEN_PATH
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _save_token(token: dict) -> None:
    path = GOOGLE_TOKEN_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(token), encoding="utf-8")
    temporary.replace(path)
    path.chmod(0o600)


def _expired(token: dict) -> bool:
    expiry = token.get("expiry")
    if not isinstance(expiry, (int, float)):
        return True
    return time.time() >= float(expiry) - 60


def _store_token_response(payload: dict, previous: dict | None) -> str:
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise CalendarError("Google did not return an access token.")
    refresh = payload.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        refresh = (previous or {}).get("refresh_token")
    expires_in = payload.get("expires_in")
    expiry = time.time() + (int(expires_in) if isinstance(expires_in, (int, float)) else 3600)
    saved = {"access_token": access, "expiry": expiry}
    if isinstance(refresh, str) and refresh:
        saved["refresh_token"] = refresh
    _save_token(saved)
    return access


def _refresh(token: dict) -> str:
    client_id, client_secret, _auth_uri, token_uri = _load_client()
    payload = _post_form(
        token_uri,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": str(token["refresh_token"]),
            "grant_type": "refresh_token",
        },
    )
    return _store_token_response(payload, token)


def _interactive_login() -> str:
    client_id, client_secret, auth_uri, token_uri = _load_client()
    state = secrets.token_urlsafe(24)
    result = _listen_for_code(client_id, auth_uri, state)
    if result.get("error"):
        raise CalendarError("Google sign-in was cancelled.")
    if result.get("state") != state:
        raise CalendarError("Google sign-in did not match this request. Run the command again.")
    code = result.get("code")
    if not code:
        raise CalendarError("Google sign-in did not return a code.")
    payload = _post_form(
        token_uri,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": result["redirect_uri"],
        },
    )
    previous = _load_token()
    access = _store_token_response(payload, previous)
    saved = _load_token() or {}
    if "refresh_token" not in saved:
        print(
            "Google did not return a refresh token. The next run may ask you to sign in again.",
            file=sys.stderr,
        )
    return access


def _listen_for_code(client_id: str, auth_uri: str, state: str) -> dict[str, str]:
    captured: dict[str, str] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if "code" in query or "error" in query:
                captured["code"] = query.get("code", [""])[0]
                captured["state"] = query.get("state", [""])[0]
                captured["error"] = query.get("error", [""])[0]
            body = (
                "<!DOCTYPE html><html><body><p>Google Calendar connected. "
                "You can close this tab.</p></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    url = f"{auth_uri}?{query}"
    print("Opening a browser to connect Google Calendar…", file=sys.stderr)
    print(f"If it does not open, visit:\n{url}", file=sys.stderr)
    webbrowser.open(url)
    deadline = time.time() + 180
    try:
        while time.time() < deadline and "code" not in captured and "error" not in captured:
            server.timeout = max(1, int(deadline - time.time()))
            server.handle_request()
            if captured.get("error"):
                break
    finally:
        server.server_close()
    if "code" not in captured and not captured.get("error"):
        raise CalendarError("Timed out waiting for Google sign-in.")
    captured["redirect_uri"] = redirect_uri
    return captured


def _post_form(url: str, fields: dict[str, str]) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise _error_from_http(exc) from exc
    except urllib.error.URLError as exc:
        raise CalendarError(f"Google sign-in failed: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise CalendarError("Google returned an unexpected sign-in response.")
    if payload.get("error"):
        raise CalendarError("Google rejected the sign-in. Run the command again.")
    return payload


def _api_get(url: str, access: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise _error_from_http(exc) from exc
    except urllib.error.URLError as exc:
        raise CalendarError(f"Google Calendar request failed: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise CalendarError("Google Calendar returned an unexpected response.")
    return payload


def _error_from_http(exc: urllib.error.HTTPError) -> CalendarError:
    message = ""
    try:
        body = json.loads(exc.read().decode(errors="replace"))
    except (OSError, json.JSONDecodeError):
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif isinstance(error, str):
            message = str(body.get("error_description") or error)
    if exc.code == 401:
        return _LoginExpired("Google Calendar rejected the saved login.")
    detail = f" ({message})" if message else ""
    return CalendarError(f"Google Calendar request failed ({exc.code}){detail}.")
