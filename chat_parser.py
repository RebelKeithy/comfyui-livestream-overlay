"""Parse a timed comment script into chat events.

Three input formats are understood (``fmt="auto"`` picks one by looking at the text):

* ``srt``   - ordinary SubRip blocks. The cue start time is when the comment appears.
* ``lines`` - one comment per line, chat-log style. Timestamps are optional.
* ``json``  - a list of objects, for full control (colour, badges, event type).

In the two text formats a comment is written ``username: message``. Extras:

* ``username[mod,sub]: message``   badges (mod, sub, vip, verified)
* ``username: [join]``             "joined" notice
* ``username: [follow]``           "followed" notice
* ``username: [tip $5] message``   highlighted paid/tip message
* ``[system] any text``            neutral system line

A comment with no ``username:`` prefix gets a generated username (stable per seed).

The script can also carry node settings, so whatever writes the script (a person or an
LLM) can control the overlay through the text alone. In the text formats they are
header lines such as ``# viewer_count: 1300``; in JSON, a top-level ``"settings"``
object. See ``parse_settings``.
"""
from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field

__all__ = ["ChatEvent", "parse_comments", "parse_settings", "parse_time", "SETTINGS"]


@dataclass
class ChatEvent:
    time: float
    user: str
    text: str = ""
    kind: str = "chat"  # chat | join | follow | tip | system
    color: str | None = None
    badges: list[str] = field(default_factory=list)
    amount: str = ""


_TIME = r"-?(?:\d+:)?\d+:\d+(?:[.,]\d+)?"
_SRT_CUE = re.compile(rf"({_TIME})\s*-->\s*({_TIME})")
_LINE_BRACKET = re.compile(rf"^\[\s*({_TIME}|-?\d+(?:\.\d+)?)\s*\]\s*(.+)$")
_LINE_CLOCK = re.compile(rf"^({_TIME})(?:\s*[|\-\u2013\u2014]\s*|\s+)(.+)$")
_LINE_PIPE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*\|\s*(.+)$")
_USER = re.compile(
    r"^@?(?P<user>[^\s:\[\]|]{1,32})(?:\[(?P<badges>[A-Za-z,\s]+)\])?:(?!//)\s*(?P<msg>.*)$",
    re.S,
)
_USER_ONLY = re.compile(r"^@?(?P<user>[^\s:\[\]|]{1,32})(?:\[(?P<badges>[A-Za-z,\s]+)\])?$")
_TAG = re.compile(
    r"^\[(join|joined|follow|followed|tip|gift|donation|system)(?:\s+([^\]]+))?\]\s*(.*)$",
    re.I | re.S,
)
_HTML = re.compile(r"</?[a-zA-Z][^>]*>")
_KINDS = {
    "join": "join", "joined": "join",
    "follow": "follow", "followed": "follow",
    "tip": "tip", "gift": "tip", "donation": "tip",
    "system": "system", "chat": "chat", "message": "chat",
}

_ADJ = ("quiet pixel lazy neon salty cosmic tiny turbo sleepy lucky mellow spicy fuzzy "
        "north velvet rusty lunar minty wild soft").split()
_NOUN = ("otter mango comet biscuit panda falcon noodle pebble walrus maple goblin "
         "sparrow taco willow badger koala pickle raven moth yeti").split()


def _random_username(rng: random.Random) -> str:
    a, b, style = rng.choice(_ADJ), rng.choice(_NOUN), rng.random()
    if style < 0.35:
        return f"{a}{b}{rng.randint(1, 99)}"
    if style < 0.60:
        return f"{a}_{b}"
    if style < 0.80:
        return f"{b}{rng.randint(100, 9999)}"
    return f"{a.capitalize()}{b.capitalize()}"


def parse_time(value) -> float:
    """'12.5', '12.5s', '0:12', '00:00:12,500', '1:02:03.5', '-0:04' or a number -> seconds.

    Negative times are allowed: they put comments in the chat before the first frame.
    """
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower().rstrip("s").replace(",", ".")
    sign = -1.0 if s.startswith("-") else 1.0
    seconds = 0.0
    for part in s.lstrip("+-").split(":"):
        seconds = seconds * 60 + float(part)
    return sign * seconds


def _badges(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(b).strip().lower() for b in raw if str(b).strip()]


def _event(time, body: str, rng: random.Random, user: str | None = None) -> ChatEvent:
    """Build an event from 'user: message' text (or a bare message if ``user`` is given)."""
    body = body.strip()
    badges: list[str] = []
    if user is None:
        m = _USER.match(body)
        if m:
            user, badges, body = m.group("user"), _badges(m.group("badges")), m.group("msg").strip()
    else:
        user = user.strip()
        m = _USER_ONLY.match(user)
        if m:
            user, badges = m.group("user"), _badges(m.group("badges"))
        else:
            user = user.lstrip("@")

    kind, amount = "chat", ""
    tag = _TAG.match(body)
    if tag:
        kind = _KINDS[tag.group(1).lower()]
        amount = (tag.group(2) or "").strip() if kind == "tip" else ""
        body = tag.group(3).strip()
    if kind == "system":
        user = ""
    elif not user:
        user = _random_username(rng)
    return ChatEvent(time=time, user=user, text=body, kind=kind, badges=badges, amount=amount)


def _parse_srt(text: str, rng: random.Random) -> list[ChatEvent]:
    events = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            cue = _SRT_CUE.search(ln)
            if cue:
                body = _HTML.sub("", " ".join(lines[i + 1:]))
                if body:
                    events.append(_event(parse_time(cue.group(1)), body, rng))
                break
    return events


def _parse_lines(text: str, rng: random.Random) -> list[ChatEvent]:
    events = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        time = None
        for pattern in (_LINE_BRACKET, _LINE_CLOCK, _LINE_PIPE):
            m = pattern.match(ln)
            if m:
                time, ln = parse_time(m.group(1)), m.group(2).strip()
                break
        if "|" in ln and not _USER.match(ln):
            user, _, msg = ln.partition("|")
            events.append(_event(time, msg, rng, user=user))
        else:
            events.append(_event(time, ln, rng))
    return events


def _first(obj: dict, *keys, default=None):
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return default


def _parse_json(text: str, rng: random.Random) -> list[ChatEvent]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = _first(data, "comments", "messages", "events", "chat", default=[])
    events = []
    for item in data:
        if isinstance(item, str):
            events.append(_event(None, item, rng))
            continue
        raw_time = _first(item, "time", "t", "start", "timestamp")
        user = str(_first(item, "user", "username", "name", "author", default="")).strip()
        body = str(_first(item, "text", "message", "msg", "comment", "body", default=""))
        ev = _event(None if raw_time is None else parse_time(raw_time), body, rng, user=user)
        kind = _KINDS.get(str(_first(item, "type", "kind", default="")).lower())
        if kind:
            ev.kind = kind
            if kind == "system":
                ev.user = ""
        ev.color = _first(item, "color", "colour")
        ev.badges = _badges(_first(item, "badges", "badge")) or ev.badges
        ev.amount = str(_first(item, "amount", default=ev.amount))
        events.append(ev)
    return events


def detect_format(text: str) -> str:
    stripped = text.lstrip()
    if stripped[:1] in "[{":
        try:
            if isinstance(json.loads(stripped), (list, dict)):
                return "json"
        except ValueError:
            pass
    return "srt" if _SRT_CUE.search(text) else "lines"


# ---------------------------------------------------------------------------- settings

def _number(lo, hi, integer=False):
    def convert(raw):
        s = str(raw).strip().lower().replace(",", "").replace("_", "")
        scale = 1.0
        if s[-1:] in ("k", "m"):
            scale, s = (1e3 if s[-1] == "k" else 1e6), s[:-1]
        value = float(s) * scale
        if not math.isfinite(value):
            raise ValueError("not a finite number")
        value = min(hi, max(lo, value))
        return int(round(value)) if integer else value
    return convert


def _choice(options: dict):
    def convert(raw):
        return options[str(raw).strip().lower()]  # KeyError -> rejected
    return convert


def _text(raw):
    return str(raw).strip().strip("\"'").strip()[:80]


def _hex_color(raw):
    s = str(raw).strip().lower()
    if not re.fullmatch(r"#?(?:[0-9a-f]{3}|[0-9a-f]{6})", s):
        raise ValueError("not a hex colour")
    return "#" + s.lstrip("#")


# Settings a script may carry: name -> converter. Names match the node's inputs.
SETTINGS = {
    "streamer_name": _text,
    "stream_title": _text,
    "viewer_count": _number(1, 50_000_000, integer=True),
    "layout": _choice({"mobile": "mobile", "desktop": "desktop"}),
    "desktop_panel": _choice({"extend": "extend canvas", "extend canvas": "extend canvas",
                              "overlay": "overlay video", "overlay video": "overlay video"}),
    "accent_color": _hex_color,
    "reaction_rate": _number(0.0, 30.0),
    "ui_scale": _number(0.4, 3.0),
    "chat_bg_opacity": _number(0.0, 1.0),
    "uptime_minutes": _number(0.0, 6000.0),
    "seed": _number(0, 0xFFFFFFFFFFFFFFFF, integer=True),
}
_SETTING_ALIASES = {
    "streamer": "streamer_name", "title": "stream_title", "viewers": "viewer_count",
    "accent": "accent_color", "reactions": "reaction_rate", "chat_bg": "chat_bg_opacity",
    "uptime": "uptime_minutes", "panel": "desktop_panel", "scale": "ui_scale",
}
_SETTING_LINE = re.compile(r"^\s*#\s*([A-Za-z][A-Za-z _-]*?)\s*[:=]\s*(.*?)\s*$")


def parse_settings(text: str, fmt: str = "auto"):
    """Read node settings embedded in a comment script.

    Text formats: header lines ``# name: value`` (or ``# name = value``).
    JSON: ``{"settings": {"name": value, ...}, "comments": [...]}``.

    Returns ``(settings, rejected)``. ``settings`` maps node input names to converted,
    range-clamped values. ``rejected`` lists ``(name, raw_value)`` pairs whose value
    could not be used. ``#`` lines that do not name a known setting are ordinary
    comments and are ignored silently.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if fmt not in ("srt", "lines", "json"):
        fmt = detect_format(text) if text.strip() else "lines"
    raw = []
    if fmt == "json":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        block = data.get("settings") if isinstance(data, dict) else None
        if isinstance(block, dict):
            raw = list(block.items())
    else:
        for line in text.splitlines():
            m = _SETTING_LINE.match(line)
            if m:
                raw.append((m.group(1), m.group(2)))

    settings, rejected = {}, []
    for name, value in raw:
        key = re.sub(r"[\s-]+", "_", str(name).strip().lower())
        key = _SETTING_ALIASES.get(key, key)
        if key not in SETTINGS or value is None or str(value).strip() == "":
            continue
        try:
            settings[key] = SETTINGS[key](value)
        except (ValueError, KeyError, IndexError):
            rejected.append((key, str(value)))
    return settings, rejected


def parse_comments(text: str, fmt: str = "auto", seed: int = 0,
                   auto_interval: float = 1.6) -> list[ChatEvent]:
    """Parse ``text`` and return events sorted by time.

    Comments without a timestamp are placed after the previous one, spaced by roughly
    ``auto_interval`` seconds with random jitter so the rhythm does not look mechanical.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if not text.strip():
        return []
    rng = random.Random(f"chat-{seed}")
    if fmt not in ("srt", "lines", "json"):
        fmt = detect_format(text)
    events = {"srt": _parse_srt, "lines": _parse_lines, "json": _parse_json}[fmt](text, rng)

    last = 0.0
    for ev in events:
        if ev.time is None:
            ev.time = last + max(0.05, auto_interval) * rng.uniform(0.35, 1.7)
        last = ev.time
    events.sort(key=lambda e: e.time)
    return events
