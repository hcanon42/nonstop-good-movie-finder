"""HTTP fetches with a browser user agent and a few retries."""

from __future__ import annotations

import http.cookiejar
import time
import urllib.error
import urllib.request
from typing import Any

from goodmoviefinder.config import USER_AGENT

def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }


def make_cookie_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _read_response(resp: Any) -> str:
    charset = resp.headers.get_content_charset() or "utf-8"
    return resp.read().decode(charset, errors="replace")


def fetch(
    url: str,
    timeout: float = 45.0,
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    req = urllib.request.Request(url, headers=_request_headers())
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if opener is not None:
                resp_cm = opener.open(req, timeout=timeout)
            else:
                resp_cm = urllib.request.urlopen(req, timeout=timeout)
            with resp_cm as resp:
                return _read_response(resp)
        except urllib.error.HTTPError:
            raise
        except TimeoutError as exc:
            last_error = urllib.error.URLError(f"timed out: {exc}")
        except urllib.error.URLError as exc:
            last_error = exc
        if attempt == 2:
            break
        time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def fetch_status(url: str, timeout: float = 45.0) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.geturl(), _read_response(resp)
        except urllib.error.HTTPError:
            raise
        except TimeoutError as exc:
            last_error = urllib.error.URLError(f"timed out: {exc}")
        except urllib.error.URLError as exc:
            last_error = exc
        if attempt == 2:
            break
        time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error
