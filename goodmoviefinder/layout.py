"""Shared page shell: top bar, account menu, page header, tabs, assets."""

from __future__ import annotations

import html
import json
from pathlib import Path

from goodmoviefinder.config import DEFAULT_KNOWN_LANGUAGES

_ASSET_DIR = Path(__file__).resolve().parent

NONSTOP_HREF = "program-ranked.html"
ACCOUNT_HREF = "account.html"


def json_for_html(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")


def _read(name: str) -> str:
    return (_ASSET_DIR / name).read_text(encoding="utf-8")


def _account_avatar_markup(initial: str, avatar_url: str | None) -> str:
    if avatar_url and avatar_url.startswith("https://"):
        safe = html.escape(avatar_url, quote=True)
        return f'<img class="account-avatar" src="{safe}" alt="" aria-hidden="true">'
    return f'<span class="account-avatar" aria-hidden="true">{initial}</span>'


def _profile_form(user: str, signed_in: bool) -> str:
    label = "Switch account" if signed_in else "Letterboxd username"
    button = "Load" if signed_in else "Sign in"
    value = html.escape(user, quote=True) if signed_in else ""
    return (
        '<form data-profile-form>'
        f'<label class="menu-label">{label}</label>'
        '<div class="settings-user-row">'
        '<span class="settings-at" aria-hidden="true">@</span>'
        f'<input name="letterboxd" value="{value}" placeholder="username" '
        'autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" '
        f'aria-label="Letterboxd username">'
        f'<button type="submit" class="btn btn-primary settings-load">{button}</button>'
        "</div>"
        '<p class="settings-status" role="status"></p>'
        "</form>"
    )


def sign_in_card(user: str = "") -> str:
    """Centered prompt shown in place of personal content when signed out."""
    return (
        '<div class="signin-card panel">'
        "<h2>Sign in with Letterboxd</h2>"
        "<p>Enter your Letterboxd username to see your watchlist, "
        "where it can be streamed, and films picked for you. No password is needed.</p>"
        + _profile_form(user, False)
        + "</div>"
    )


def account_menu_html(
    user: str,
    signed_in: bool,
    choices: list[str],
    selected: list[str],
    *,
    following_user: str | None = None,
    avatar_url: str | None = None,
) -> str:
    selected_keys = {name.casefold() for name in selected}
    boxes = "".join(
        '<label class="lang-option">'
        f'<input type="checkbox" name="language" value="{html.escape(name, quote=True)}"'
        f'{" checked" if name.casefold() in selected_keys else ""}>'
        f"<span>{html.escape(name)}</span></label>"
        for name in choices
    )
    safe_user = html.escape(user)
    active_user = (following_user or user).strip()
    own_profile = signed_in and active_user.casefold() == user.casefold()
    if signed_in:
        initial = html.escape(active_user[:1] or "?")
        chip_avatar = _account_avatar_markup(
            initial,
            avatar_url if own_profile else None,
        )
        name = f"@{html.escape(active_user)}"
        profile_link = ""
        if own_profile:
            profile_link = (
                f'<a class="account-letterboxd" href="https://letterboxd.com/{html.escape(user, quote=True)}/" '
                'target="_blank" rel="noopener noreferrer" '
                f'aria-label="Open @{safe_user} on Letterboxd">'
                '<span class="account-letterboxd-mark" aria-hidden="true"></span></a>'
            )
        who = (
            '<p class="menu-who">Signed in as '
            f'<a href="https://letterboxd.com/{html.escape(user, quote=True)}/" '
            f'target="_blank" rel="noopener noreferrer">@{safe_user}</a></p>'
        )
        signout = (
            '<div class="menu-section menu-actions">'
            '<button type="button" id="account-signout" class="btn btn-danger">Sign out</button>'
            "</div>"
        )
        signed_class = ""
        account_open = (
            '<div class="account-chip">'
            '<button type="button" class="account-open account-settings-open" id="settings-open" '
            'aria-expanded="false" aria-controls="settings-panel" aria-haspopup="dialog" '
            'aria-label="Account and languages">'
            f"{chip_avatar}"
            '<span class="account-caret" aria-hidden="true"></span></button>'
            '<button type="button" class="account-name" id="account-switch-open" '
            'aria-expanded="false" aria-controls="account-switch-panel" aria-haspopup="listbox">'
            f"{name}</button>{profile_link}</div>"
            '<div id="account-switch-panel" class="account-switch-panel" role="listbox" '
            'aria-label="Switch Letterboxd profile" hidden>'
            '<p class="account-switch-status muted" role="status">Loading…</p>'
            '<ul class="account-switch-list"></ul></div>'
        )
    else:
        initial = "?"
        name = "Sign in"
        who = '<p class="menu-who">You are not signed in.</p>'
        signout = ""
        signed_class = " is-signed-out"
        account_open = (
            '<button type="button" class="account-open" id="settings-open" '
            'aria-expanded="false" aria-controls="settings-panel" aria-haspopup="dialog">'
            f'<span class="account-avatar" aria-hidden="true">{initial}</span>'
            f'<span class="account-name">{name}</span>'
            '<span class="account-caret" aria-hidden="true"></span></button>'
        )
    return (
        f'<div class="account{signed_class}">'
        f"{account_open}"
        '<div id="settings-panel" class="settings-panel" role="dialog" '
        'aria-label="Account and languages" hidden>'
        f'<div class="menu-section">{who}{_profile_form(user, signed_in)}</div>'
        '<div class="menu-section"><fieldset class="settings-languages">'
        "<legend>Languages I follow</legend>"
        f'<div class="lang-options">{boxes}</div>'
        '<p class="settings-hint">Original versions count for these languages. '
        "Every other film needs English subtitles.</p>"
        "</fieldset>"
        '<p><button type="button" id="settings-reset" class="btn">Reset languages</button></p>'
        "</div>"
        f"{signout}"
        "</div></div>"
    )


def render_page(
    *,
    page: str,
    title: str,
    heading: str,
    tagline: str,
    stats: list[str],
    hint_html: str,
    tabs: list[tuple[str, str]],
    tools_html: str,
    panels_html: str,
    user: str,
    signed_in: bool,
    choices: list[str],
    selected: list[str],
    viewer: dict[str, object],
    scripts: tuple[str, ...],
    styles: tuple[str, ...],
    extra_json: str = "",
    footer: str = "",
) -> str:
    """Compose one page: top bar, header, tab bar, tools row, panels."""

    def switch(key: str, href: str, label: str) -> str:
        current = ' aria-current="page"' if key == page else ""
        return f'<a href="{href}"{current}>{label}</a>'

    tab_html = "".join(
        f'<button type="button" role="tab" class="tab" data-tab="{tab_id}" '
        f'aria-selected="{"true" if index == 0 else "false"}" '
        f'aria-controls="{tab_id}-view" id="tab-{tab_id}">{label}</button>'
        for index, (tab_id, label) in enumerate(tabs)
    )
    stats_html = (
        '<ul class="page-stats">' + "".join(f"<li>{item}</li>" for item in stats) + "</ul>"
        if stats
        else ""
    )
    css = "".join(_read(name) for name in styles)
    js = "".join(f"  <script>\n{_read(name)}  </script>\n" for name in scripts)
    viewer_payload = {
        "letterboxdUser": user if signed_in else "",
        "signedIn": signed_in,
        "languages": selected,
        "defaults": list(DEFAULT_KNOWN_LANGUAGES),
    }
    viewer_payload.update(viewer)
    following = str(viewer.get("followingUser") or user)
    raw_avatar = viewer.get("letterboxdAvatar")
    avatar_url = raw_avatar if isinstance(raw_avatar, str) and raw_avatar.startswith("https://") else None
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
{css}  </style>
</head>
<body class="page-{page}" data-page="{page}">
  <header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="{NONSTOP_HREF}">Nonstop <b>&times;</b> Letterboxd</a>
      <nav class="page-switch" aria-label="Site">
        {switch("nonstop", NONSTOP_HREF, "Nonstop Kino")}
        {switch("account", ACCOUNT_HREF, "My Letterboxd")}
      </nav>
      {account_menu_html(user, signed_in, choices, selected, following_user=following, avatar_url=avatar_url)}
    </div>
  </header>
  <section class="page-head">
    <h1>{html.escape(heading)}</h1>
    <p class="tagline">{html.escape(tagline)}</p>
    {stats_html}
    {hint_html}
  </section>
  <div class="tabs-bar">
    <div class="tabs" role="tablist" aria-label="{html.escape(heading)} views">{tab_html}</div>
  </div>
  <main>
    <div class="tab-tools">
{tools_html}
    </div>
{panels_html}
  </main>
  <footer>{footer}</footer>
  <script type="application/json" id="viewer-config">{json_for_html(viewer_payload)}</script>
{extra_json}{js}</body>
</html>"""
