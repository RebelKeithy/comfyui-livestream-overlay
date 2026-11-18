"""Livestream overlay renderer (Pillow + numpy only, no torch / ComfyUI imports).

``OverlayRenderer.render(t)`` returns the overlay for time ``t`` as an RGBA image;
``OverlayRenderer.composite(frame, t)`` blends it onto a float frame.

Everything is drawn from scratch as a generic, unbranded stream UI. Shapes are
supersampled and text is composited through alpha masks so edges stay clean on
top of arbitrary video.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import os
import random
import re
import sys
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

__all__ = ["OverlayConfig", "OverlayRenderer"]

SS = 4  # supersampling factor for shapes
WHITE = (255, 255, 255, 255)
LIVE_RED = (236, 44, 64)
PANEL_BG = (24, 24, 28)
PANEL_LINE = (48, 48, 56)
PANEL_FIELD = (38, 38, 45)
PANEL_TEXT = (236, 236, 240)
PANEL_DIM = (160, 160, 172)

USER_COLORS = [
    (255, 107, 107), (255, 159, 67), (254, 202, 87), (29, 209, 161), (72, 219, 251),
    (84, 160, 255), (165, 130, 255), (255, 121, 198), (0, 210, 211), (120, 224, 143),
    (250, 130, 49), (214, 162, 232),
]
HEART_COLORS = [(255, 71, 107), (255, 120, 150), (255, 170, 60), (255, 92, 92), (190, 120, 255)]
BADGE_STYLE = {  # badge name -> (background colour, icon shape)
    "mod": ((34, 170, 90), "shield"),
    "sub": (None, "star"),  # None = accent colour
    "vip": ((224, 64, 160), "diamond"),
    "verified": ((60, 140, 255), "check"),
}


@dataclass
class OverlayConfig:
    layout: str = "mobile"          # "mobile" (chat over video) | "desktop" (chat side panel)
    panel_mode: str = "extend"      # desktop only: "extend" the canvas | "overlay" the video
    streamer_name: str = "streamer"
    stream_title: str = ""
    viewer_count: int = 1280
    accent: str = "#6d5efc"
    reaction_rate: float = 2.0      # floating hearts per second (mobile layout), 0 = off
    ui_scale: float = 1.0
    chat_bg_opacity: float = 0.0    # mobile message bubbles: 0 = text only, ~0.45 = dark bubble
    uptime_minutes: float = 47.0    # how long the stream has "already been live" at t=0
    seed: int = 0


# --------------------------------------------------------------------------- helpers

def _hex(value: str, default=(109, 94, 252)):
    s = (value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        if len(s) != 6:
            raise ValueError
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def _mix(c1, c2, k):
    return tuple(round(a + (b - a) * k) for a, b in zip(c1, c2))


def user_color(name: str):
    digest = hashlib.md5(name.lower().encode("utf-8")).hexdigest()
    return USER_COLORS[int(digest, 16) % len(USER_COLORS)]


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


@lru_cache(maxsize=512)
def rounded_mask(w: int, h: int, r: float) -> Image.Image:
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0.0, min(float(r), w / 2, h / 2))
    big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=r * SS, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def _heart_points(n=72):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((0.5 + x / 36, 0.46 - y / 36))
    return pts


def _star_points():
    pts = []
    for i in range(10):
        radius = 0.36 if i % 2 == 0 else 0.15
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((0.5 + radius * math.cos(a), 0.52 + radius * math.sin(a)))
    return pts


_SHAPES = {
    "heart": _heart_points(),
    "star": _star_points(),
    "diamond": [(0.5, 0.16), (0.82, 0.5), (0.5, 0.84), (0.18, 0.5)],
    "shield": [(0.24, 0.2), (0.76, 0.2), (0.76, 0.54), (0.5, 0.84), (0.24, 0.54)],
    "check": [(0.2, 0.52), (0.31, 0.41), (0.44, 0.54), (0.69, 0.27), (0.8, 0.38), (0.44, 0.77)],
}


@lru_cache(maxsize=512)
def shape_mask(name: str, size: int) -> Image.Image:
    size = max(2, int(size))
    big = Image.new("L", (size * SS, size * SS), 0)
    draw = ImageDraw.Draw(big)
    if name == "person":
        s = size * SS
        draw.ellipse((s * 0.32, s * 0.08, s * 0.68, s * 0.44), fill=255)
        draw.pieslice((s * 0.12, s * 0.54, s * 0.88, s * 1.42), 180, 360, fill=255)
    else:
        draw.polygon([(x * size * SS, y * size * SS) for x, y in _SHAPES[name]], fill=255)
    return big.resize((size, size), Image.LANCZOS)


def tint(mask: Image.Image, rgb, alpha: int = 255) -> Image.Image:
    """Solid colour sprite whose alpha is ``mask`` (straight alpha, no dark fringes)."""
    sprite = Image.new("RGBA", mask.size, tuple(rgb[:3]) + (0,))
    sprite.putalpha(mask if alpha >= 255 else mask.point(lambda v: v * alpha // 255))
    return sprite


def comp(dst: Image.Image, src: Image.Image, x, y, opacity: float = 1.0) -> None:
    """Alpha-composite ``src`` onto ``dst`` at (x, y), clipped to ``dst``."""
    if opacity <= 0:
        return
    x, y = int(round(x)), int(round(y))
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(src.width, dst.width - x), min(src.height, dst.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    if (sx0, sy0, sx1, sy1) != (0, 0, src.width, src.height):
        src = src.crop((sx0, sy0, sx1, sy1))
    if opacity < 1:
        src = src.copy()
        src.putalpha(src.getchannel("A").point(lambda v: int(v * opacity)))
    dst.alpha_composite(src, (x + sx0, y + sy0))


def draw_text(dst: Image.Image, x, y, text: str, font, fill, anchor: str = "ls") -> None:
    if not text:
        return
    l, t, r, b = font.getbbox(text, anchor=anchor)
    l, t, r, b = math.floor(l) - 1, math.floor(t) - 1, math.ceil(r) + 1, math.ceil(b) + 1
    mask = Image.new("L", (r - l, b - t), 0)
    ImageDraw.Draw(mask).text((-l, -t), text, font=font, fill=255, anchor=anchor)
    alpha = fill[3] if len(fill) > 3 else 255
    comp(dst, tint(mask, fill, alpha), round(x) + l, round(y) + t)


def ellipsize(text: str, font, max_w: float) -> str:
    if font.getlength(text) <= max_w:
        return text
    while text and font.getlength(text + "\u2026") > max_w:
        text = text[:-1]
    return text.rstrip() + "\u2026"


def compact_count(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K"
    return f"{n / 1_000_000:.1f}M"


# --------------------------------------------------------------------------- fonts

_PKG_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_EXT = (".ttf", ".otf", ".ttc")
_TEXT_FONTS = [  # (regular, bold) in order of preference; "#n" selects a face in a .ttc
    ("inter-regular.otf", "inter-bold.otf"), ("inter-regular.ttf", "inter-bold.ttf"),
    ("roboto-regular.ttf", "roboto-bold.ttf"),
    ("segoeui.ttf", "segoeuib.ttf"),
    ("helveticaneue.ttc", "helveticaneue.ttc#1"), ("helvetica.ttc", "helvetica.ttc#1"),
    ("arial.ttf", "arialbd.ttf"), ("arial.ttf", "arial bold.ttf"),
    ("notosans-regular.ttf", "notosans-bold.ttf"),
    ("liberationsans-regular.ttf", "liberationsans-bold.ttf"),
    ("dejavusans.ttf", "dejavusans-bold.ttf"),
]
_EMOJI_FONTS = ["seguiemj.ttf", "apple color emoji.ttc", "notocoloremoji.ttf",
                "notocoloremoji-regular.ttf", "twemoji.ttf"]


def _system_font_dirs():
    if sys.platform.startswith("win"):
        dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        dirs = ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts",
                os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if d and os.path.isdir(d)]


@lru_cache(maxsize=1)
def _font_index():
    index = {}
    for d in _system_font_dirs():
        for root, _, files in os.walk(d):
            for name in files:
                if name.lower().endswith(_FONT_EXT):
                    index.setdefault(name.lower(), os.path.join(root, name))
    return index


def _resolve(spec: str, index):
    name, _, face = spec.partition("#")
    path = index.get(name)
    return (path, int(face or 0)) if path else None


def _find_fonts():
    """Return ((regular_path, face), (bold_path, face), emoji_path) - any may be None."""
    regular = bold = emoji = None
    if os.path.isdir(_PKG_FONTS):  # fonts dropped into ./fonts win over system fonts
        for name in sorted(os.listdir(_PKG_FONTS)):
            if not name.lower().endswith(_FONT_EXT):
                continue
            path = os.path.join(_PKG_FONTS, name)
            if "emoji" in name.lower():
                emoji = emoji or path
            elif "bold" in name.lower():
                bold = bold or (path, 0)
            elif "italic" not in name.lower():
                regular = regular or (path, 0)
    index = _font_index()
    if regular is None:
        for reg_name, bold_name in _TEXT_FONTS:
            reg, bld = _resolve(reg_name, index), _resolve(bold_name, index)
            if reg and bld:
                regular, bold = reg, bold or bld
                break
    if emoji is None:
        emoji = next((index[n] for n in _EMOJI_FONTS if n in index), None)
    return regular, bold or regular, emoji


_E = "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]"
_TONE = "[\U0001F3FB-\U0001F3FF]"
EMOJI_RE = re.compile(
    f"(?:[\U0001F1E6-\U0001F1FF]{{2}}|{_E}\uFE0F?{_TONE}?(?:\u200D{_E}\uFE0F?{_TONE}?)*)"
)


class Fonts:
    def __init__(self):
        self.regular, self.bold, self.emoji_path = _find_fonts()
        self._cache = {}
        self._emoji_fonts = {}
        self._emoji_cache = {}

    def get(self, size: int, bold: bool = False):
        size = max(6, int(size))
        key = (size, bold)
        if key not in self._cache:
            spec = self.bold if bold else self.regular
            font = None
            if spec:
                try:
                    font = ImageFont.truetype(spec[0], size, index=spec[1])
                except OSError:
                    font = None
            if font is None:
                try:
                    font = ImageFont.load_default(size)  # Pillow >= 10.1
                except TypeError:
                    font = ImageFont.load_default()
            self._cache[key] = font
        return self._cache[key]

    def _emoji_font(self, size: int):
        if size not in self._emoji_fonts:
            try:
                self._emoji_fonts[size] = ImageFont.truetype(self.emoji_path, size)
            except (OSError, ValueError):
                self._emoji_fonts[size] = None
        return self._emoji_fonts[size]

    def emoji(self, cluster: str, height: int):
        """Colour emoji sprite scaled to ``height`` px, or None if it cannot be drawn."""
        key = (cluster, height)
        if key in self._emoji_cache:
            return self._emoji_cache[key]
        sprite = None
        if self.emoji_path:
            text = cluster.replace("\uFE0F", "")
            # Bitmap emoji fonts only load at their native strike size, so try a few.
            for size in (height, 109, 136, 160, 96, 64, 48, 40, 32, 20):
                font = self._emoji_font(size)
                if font is None:
                    continue
                try:
                    l, t, r, b = font.getbbox(text)
                    if r - l <= 0 or b - t <= 0:
                        continue
                    glyph = Image.new("RGBA", (r - l, b - t), (0, 0, 0, 0))
                    ImageDraw.Draw(glyph).text((-l, -t), text, font=font, embedded_color=True)
                except (OSError, ValueError):
                    continue
                box = glyph.getbbox()
                if box:
                    glyph = glyph.crop(box)
                    width = max(1, round(glyph.width * height / glyph.height))
                    sprite = glyph.resize((width, height), Image.LANCZOS)
                break
        self._emoji_cache[key] = sprite
        return sprite


# --------------------------------------------------------------------------- rich text

@dataclass
class Atom:
    w: float
    text: str = ""
    font: object = None
    fill: tuple = WHITE
    img: Image.Image | None = None
    drop: int = 0  # how far an inline image hangs below the baseline


def flow(words, max_w: float, space_w: float):
    """Word-wrap a list of words (each a list of Atoms) -> lines of (x, Atom)."""
    lines, x = [[]], 0.0
    for word in words:
        word_w = sum(a.w for a in word)
        if lines[-1]:
            if x + space_w + word_w > max_w:
                lines.append([])
                x = 0.0
            else:
                x += space_w
        if word_w <= max_w:
            for a in word:
                lines[-1].append((x, a))
                x += a.w
            continue
        for a in word:  # a single word wider than the column: break it by character
            pieces = [a] if a.img is not None else [
                Atom(w=a.font.getlength(ch), text=ch, font=a.font, fill=a.fill) for ch in a.text]
            for piece in pieces:
                if x + piece.w > max_w and x > 0:
                    lines.append([])
                    x = 0.0
                lines[-1].append((x, piece))
                x += piece.w
    return lines


def lines_width(lines) -> float:
    return max((x + a.w for line in lines for x, a in line), default=0.0)


# --------------------------------------------------------------------------- renderer

@dataclass
class _Heart:
    t0: float
    life: float
    rise: float
    amp: float
    freq: float
    phase: float
    drift: float
    size: float
    color: tuple


class OverlayRenderer:
    def __init__(self, width: int, height: int, events, cfg: OverlayConfig,
                 end_time: float = 60.0, avatar: Image.Image | None = None):
        self.W, self.H = int(width), int(height)
        self.cfg = cfg
        self.events = sorted(events, key=lambda e: e.time)
        self.times = [e.time for e in self.events]
        self.end_time = max(1.0, float(end_time))
        self.fonts = Fonts()
        self.accent = _hex(cfg.accent)
        r, g, b = self.accent
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        self.on_accent = (255, 255, 255, 255) if luminance < 0.62 else (20, 20, 24, 255)
        self.mobile = cfg.layout != "desktop"
        self._sprites = {}
        self._small = {}
        self._viewers = self._viewer_series()
        self._hearts, self._heart_t0 = [], []
        self.fade_mask = None
        if self.mobile:
            self._setup_mobile(avatar)
        else:
            self._setup_desktop(avatar)

    # ----------------------------------------------------------------- public

    def render(self, t: float) -> Image.Image:
        """Overlay at time ``t`` (seconds) as an RGBA image of ``self.out_size``."""
        img = self._static.copy()
        comp(img, self._chat_layer(t), self.chat_x, self.chat_y)
        if self.mobile:
            x, y, _, _ = self._viewer_box
            comp(img, self._viewer_pill(compact_count(self.viewers_at(t))), x, y)
            self._draw_hearts(img, t)
        else:
            self._draw_desktop_dynamic(img, t)
        return img

    def composite(self, frame: np.ndarray, t: float) -> np.ndarray:
        """Blend the overlay onto ``frame`` (float HxWx3+, 0..1). Returns float32 HxWx3."""
        overlay = np.asarray(self.render(t), dtype=np.float32) / 255.0
        alpha = overlay[..., 3:4]
        out_w, out_h = self.out_size
        frame = np.asarray(frame, dtype=np.float32)[..., :3]
        if (out_w, out_h) != (self.W, self.H):
            base = np.zeros((out_h, out_w, 3), dtype=np.float32)
            base[: self.H, : self.W] = frame
        else:
            base = frame
        return base * (1.0 - alpha) + overlay[..., :3] * alpha

    def viewers_at(self, t: float) -> int:
        return self._viewers[min(len(self._viewers) - 1, max(0, int(t / 3.0)))]

    # ----------------------------------------------------------------- shared pieces

    def _viewer_series(self):
        """Viewer count sampled every 3 s: a slow, mean-reverting random walk."""
        rng = random.Random(f"viewers-{self.cfg.seed}")
        base = max(1, int(self.cfg.viewer_count))
        value, series = float(base), []
        for _ in range(int(self.end_time / 3.0) + 3):
            series.append(max(1, int(round(value))))
            value += (base - value) * 0.05 + rng.gauss(base * 0.002, max(0.6, base * 0.006))
        return series

    def _scrim(self, base, x, y, w, h, strength, dark_at_top):
        if w <= 0 or h <= 0:
            return
        grad = Image.linear_gradient("L").resize((w, h))  # 0 at the top -> 255 at the bottom
        if dark_at_top:
            grad = ImageChops.invert(grad)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.putalpha(grad.point(lambda v: int(v * v / 255 * strength)))
        comp(base, layer, x, y)

    def _avatar(self, picture, d: int, name: str) -> Image.Image:
        circle = rounded_mask(d, d, d / 2)
        if picture is not None:
            side = min(picture.size)
            left, top = (picture.width - side) // 2, (picture.height - side) // 2
            img = picture.convert("RGB").crop((left, top, left + side, top + side))
            img = img.resize((d, d), Image.LANCZOS).convert("RGBA")
            img.putalpha(circle)
            return img
        img = tint(circle, user_color(name or "?"))
        initial = next((c for c in name if c.isalnum()), "?").upper()
        draw_text(img, d / 2, d / 2, initial, self.fonts.get(round(d * 0.5), True), WHITE, "mm")
        return img

    def _badge(self, name: str, size: int):
        style = BADGE_STYLE.get(name)
        if not style:
            return None
        key = ("badge", name, size)
        if key not in self._small:
            color, shape = style
            img = tint(rounded_mask(size, size, size * 0.24), color or self.accent)
            icon = WHITE if color else self.on_accent
            comp(img, tint(shape_mask(shape, size), icon), 0, 0)
            self._small[key] = img
        return self._small[key]

    def _words(self, text: str, font, fill, fs: int):
        """Split text into words of Atoms, swapping emoji for colour sprites."""
        words = []
        for raw in text.split():
            atoms, pos = [], 0
            for m in EMOJI_RE.finditer(raw):
                if m.start() > pos:
                    chunk = raw[pos:m.start()]
                    atoms.append(Atom(w=font.getlength(chunk), text=chunk, font=font, fill=fill))
                sprite = self.fonts.emoji(m.group(), round(fs * 1.2))
                if sprite is not None:  # no emoji font -> drop it instead of drawing a "tofu" box
                    atoms.append(Atom(w=sprite.width + 1, img=sprite, drop=round(fs * 0.24)))
                pos = m.end()
            if pos < len(raw):
                chunk = raw[pos:]
                atoms.append(Atom(w=font.getlength(chunk), text=chunk, font=font, fill=fill))
            if atoms:
                words.append(atoms)
        return words

    def _badge_words(self, ev, fs: int):
        words = []
        for name in ev.badges:
            badge = self._badge(name, round(fs * 1.2))
            if badge is not None:
                words.append([Atom(w=badge.width, img=badge, drop=round(fs * 0.24))])
        return words

    def _draw_lines(self, img, lines, x0, y0, line_h, ref_font):
        ascent, descent = ref_font.getmetrics()
        for i, line in enumerate(lines):
            baseline = round(y0 + i * line_h + (line_h - (ascent + descent)) / 2 + ascent)
            for x, a in line:
                if a.img is not None:
                    comp(img, a.img, x0 + x, baseline - a.img.height + a.drop)
                else:
                    draw_text(img, x0 + x, baseline, a.text, a.font, a.fill)

    def _name_fill(self, ev, lighten: float = 0.0):
        color = _hex(ev.color, user_color(ev.user)) if ev.color else user_color(ev.user)
        return _mix(color, (255, 255, 255), lighten) + (255,)

    def _notice_text(self, ev):
        if ev.kind == "join":
            return ev.text or "joined"
        if ev.kind == "follow":
            return ev.text or "followed"
        return ev.text

    def _tip_card(self, ev, width: int) -> Image.Image:
        """Highlighted paid-message card in the accent colour."""
        u, fs = self.u, self.fs
        pad, line_h = round(10 * u), round(fs * 1.4)
        f, fb = self.fonts.get(fs), self.fonts.get(fs, True)
        ink = self.on_accent
        head = self._badge_words(ev, fs) + self._words(ev.user, fb, ink, fs)
        head += self._words(ev.amount or "sent a tip", fb, ink[:3] + (215,), fs)
        space = f.getlength(" ")
        head_lines = flow(head, width - 2 * pad, space * 1.6)
        body_lines = flow(self._words(ev.text, f, ink, fs), width - 2 * pad, space) if ev.text else []
        split = round(3 * u) if body_lines else 0
        h = 2 * pad + (len(head_lines) + len(body_lines)) * line_h + split
        card = tint(rounded_mask(width, h, 9 * u), self.accent, 245)
        self._draw_lines(card, head_lines, pad, pad, line_h, f)
        self._draw_lines(card, body_lines, pad, pad + len(head_lines) * line_h + split, line_h, f)
        return card

    def _sprite(self, i: int) -> Image.Image:
        if i not in self._sprites:
            ev = self.events[i]
            self._sprites[i] = self._sprite_mobile(ev) if self.mobile else self._sprite_desktop(ev)
        return self._sprites[i]

    def _chat_layer(self, t: float) -> Image.Image:
        """Messages stacked upward from the bottom; a new one eases in and pushes the rest."""
        layer = Image.new("RGBA", (self.chat_w, self.chat_h), (0, 0, 0, 0))
        y = float(self.chat_h)
        for i in range(bisect.bisect_right(self.times, t) - 1, -1, -1):
            sprite = self._sprite(i)
            e = _ease((t - self.times[i]) / self.anim) if self.anim > 0 else 1.0
            y -= (sprite.height + self.gap) * e
            if y + sprite.height <= 0:
                break
            comp(layer, sprite, 0, y, opacity=e)
        if self.fade_mask is not None:
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), self.fade_mask))
        return layer

    # ----------------------------------------------------------------- mobile layout

    def _setup_mobile(self, avatar):
        cfg, W, H = self.cfg, self.W, self.H
        u = self.u = min(W, H) / 400 * cfg.ui_scale
        self.fs = max(8, round(15 * u))
        self.out_size = (W, H)
        self.anim, self.gap = 0.28, round(6 * u)
        m = round(12 * u)
        base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self._scrim(base, 0, 0, W, round(H * 0.22), 0.5, dark_at_top=True)
        self._scrim(base, 0, H - round(H * 0.5), W, round(H * 0.5), 0.62, dark_at_top=False)

        # --- top row: streamer pill on the left, LIVE + viewers on the right
        top = max(m, round(H * 0.035))
        ph, inset = round(42 * u), round(4 * u)
        av = ph - 2 * inset
        f_name, f_sub = self.fonts.get(round(14 * u), True), self.fonts.get(round(11 * u))
        f_small = self.fonts.get(round(12 * u), True)

        live_w = round(f_small.getlength("LIVE") + 18 * u)
        vh = round(26 * u)
        icon = round(13 * u)
        # reserve room for the widest plausible count so the pill never changes size
        base_count = max(1, int(cfg.viewer_count))
        count_w = max(f_small.getlength(re.sub(r"\d", "8", compact_count(n)))
                      for n in (base_count // 2, base_count, base_count * 2))
        vw = round(9 * u + icon + 4 * u + count_w + 10 * u)
        vx, vy = W - m - vw, top + (ph - vh) // 2
        self._viewer_box = (vx, vy, vw, vh)
        live_x = vx - round(6 * u) - live_w
        comp(base, tint(rounded_mask(live_w, vh, 6 * u), LIVE_RED), live_x, vy)
        draw_text(base, live_x + live_w / 2, vy + vh / 2, "LIVE", f_small, WHITE, "mm")

        bw, bh = round(f_small.getlength("Follow") + 22 * u), ph - 2 * round(6 * u)
        text_max = max(30 * u, live_x - round(8 * u) - m - (inset + av + 8 * u + 10 * u + bw + 6 * u))
        text_max = min(text_max, 150 * u)
        name = ellipsize(cfg.streamer_name or "live", f_name, text_max)
        sub = ellipsize(cfg.stream_title, f_sub, text_max) if cfg.stream_title else ""
        tw = math.ceil(max(f_name.getlength(name), f_sub.getlength(sub) if sub else 0))
        pw = round(inset + av + 8 * u + tw + 10 * u + bw + 6 * u)
        comp(base, tint(rounded_mask(pw, ph, ph / 2), (0, 0, 0), 115), m, top)
        comp(base, self._avatar(avatar, av, cfg.streamer_name), m + inset, top + inset)
        tx = m + inset + av + round(8 * u)
        if sub:
            draw_text(base, tx, top + ph * 0.45, name, f_name, WHITE)
            draw_text(base, tx, top + ph * 0.80, sub, f_sub, (255, 255, 255, 195))
        else:
            draw_text(base, tx, top + ph / 2, name, f_name, WHITE, "lm")
        bx = tx + tw + round(10 * u)
        comp(base, tint(rounded_mask(bw, bh, bh / 2), self.accent), bx, top + (ph - bh) // 2)
        draw_text(base, bx + bw / 2, top + ph / 2, "Follow", f_small, self.on_accent, "mm")

        # --- bottom row: comment field + heart button
        bar_h = round(40 * u)
        bar_y = H - max(m, round(H * 0.03)) - bar_h
        heart_x = W - m - bar_h
        field_w = heart_x - round(8 * u) - m
        comp(base, tint(rounded_mask(field_w, bar_h, bar_h / 2), (0, 0, 0), 115), m, bar_y)
        draw_text(base, m + round(16 * u), bar_y + bar_h / 2, "Add a comment\u2026",
                  self.fonts.get(round(13.5 * u)), (255, 255, 255, 170), "lm")
        comp(base, tint(rounded_mask(bar_h, bar_h, bar_h / 2), (0, 0, 0), 115), heart_x, bar_y)
        hs = round(22 * u)
        comp(base, tint(shape_mask("heart", hs), HEART_COLORS[0]),
             heart_x + (bar_h - hs) // 2, bar_y + (bar_h - hs) // 2)

        # --- chat region (bottom-left) with a soft fade at the top
        self.chat_w, self.chat_h = round(W * 0.74), round(H * 0.36)
        self.chat_x, self.chat_y = m, bar_y - round(10 * u) - self.chat_h
        fade_h = max(1, round(self.chat_h * 0.22))
        self.fade_mask = Image.new("L", (self.chat_w, self.chat_h), 255)
        self.fade_mask.paste(Image.linear_gradient("L").resize((self.chat_w, fade_h)), (0, 0))

        # --- floating hearts rise from the heart button
        self._heart_origin = (heart_x + bar_h / 2, bar_y - 6 * u)
        self._heart_px = round(30 * u)
        self._init_hearts(H * 0.38)
        self._static = base

    def _viewer_pill(self, text: str) -> Image.Image:
        key = ("viewers", text)
        if key not in self._small:
            u = self.u
            _, _, w, h = self._viewer_box
            pill = tint(rounded_mask(w, h, 6 * u), (0, 0, 0), 125)
            icon = round(13 * u)
            comp(pill, tint(shape_mask("person", icon), (255, 255, 255)), round(9 * u), (h - icon) // 2)
            draw_text(pill, round(9 * u) + icon + round(4 * u), h / 2, text,
                      self.fonts.get(round(12 * u), True), WHITE, "lm")
            self._small[key] = pill
        return self._small[key]

    def _sprite_mobile(self, ev) -> Image.Image:
        u, fs = self.u, self.fs
        f, fb = self.fonts.get(fs), self.fonts.get(fs, True)
        space = f.getlength(" ")
        if ev.kind == "tip":
            return self._tip_card(ev, round(self.chat_w * 0.92))

        pad_x, pad_y, line_h = round(9 * u), round(5 * u), round(fs * 1.34)
        notice = ev.kind in ("join", "follow", "system")
        av = 0 if notice else round(fs * 1.55)
        av_gap = 0 if notice else round(7 * u)
        max_w = self.chat_w - 2 * pad_x - av - av_gap
        if notice:
            words = self._words(ev.user, fb, self._name_fill(ev, 0.45), fs) if ev.user else []
            words += self._words(self._notice_text(ev), f, (255, 255, 255, 205), fs)
        else:
            words = self._badge_words(ev, fs)
            words += self._words(ev.user, fb, self._name_fill(ev, 0.45), fs)
            words += self._words(ev.text, f, WHITE, fs)
        lines = flow(words, max_w, space)
        first_line_y = pad_y + max(0, (av - line_h) // 2)
        w = min(self.chat_w, pad_x + av + av_gap + math.ceil(lines_width(lines)) + pad_x + round(2 * u))
        h = max(pad_y + av + pad_y, first_line_y + len(lines) * line_h + pad_y)
        radius = h / 2 if len(lines) == 1 else 14 * u
        opacity = min(1.0, max(0.0, float(self.cfg.chat_bg_opacity)))
        bubble_alpha = round(255 * opacity * (0.86 if notice else 1.0))
        sprite = tint(rounded_mask(w, h, radius), (0, 0, 0), bubble_alpha)
        content = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if av:
            comp(content, self._avatar(None, av, ev.user), pad_x - round(3 * u), pad_y)
        self._draw_lines(content, lines, pad_x + av + av_gap - (round(3 * u) if av else 0),
                         first_line_y, line_h, f)
        # The lighter the bubble, the more the text needs its own soft shadow to stay
        # readable over bright video.
        shadow = max(0.0, 1.0 - opacity / 0.45)
        if shadow > 0.02:
            blur = content.getchannel("A").filter(ImageFilter.GaussianBlur(max(1.0, 1.5 * u)))
            blur = blur.point(lambda v: min(255, v * 2))
            comp(sprite, tint(blur, (0, 0, 0), round(215 * shadow)), 0, max(1, round(u)))
        comp(sprite, content, 0, 0)
        return sprite

    def _init_hearts(self, rise: float):
        rng = random.Random(f"hearts-{self.cfg.seed}")
        u = self.u

        def make(t0, rng=rng):
            return _Heart(
                t0=t0, life=rng.uniform(2.4, 3.4), rise=rise * rng.uniform(0.75, 1.1),
                amp=rng.uniform(6, 16) * u, freq=rng.uniform(0.8, 1.6),
                phase=rng.uniform(0, math.tau), drift=rng.uniform(-26, 8) * u,
                size=rng.choice((0.7, 0.85, 1.0, 1.15, 1.3)), color=rng.choice(HEART_COLORS))

        hearts = []
        if self.cfg.reaction_rate > 0:
            t = -3.5
            while t < self.end_time + 1:
                t += rng.expovariate(self.cfg.reaction_rate)
                hearts.append(make(t))
        for i, ev in enumerate(self.events):  # a small burst whenever someone tips or follows
            if ev.kind in ("tip", "follow"):
                # own generator per event, so chunked renders (different end_time) agree
                burst = random.Random(f"burst-{self.cfg.seed}-{i}")
                hearts.extend(make(ev.time + burst.uniform(0, 0.7), burst) for _ in range(6))
        hearts.sort(key=lambda h: h.t0)
        self._hearts, self._heart_t0 = hearts, [h.t0 for h in hearts]

    def _draw_hearts(self, img, t: float):
        x0, y0 = self._heart_origin
        lo = bisect.bisect_left(self._heart_t0, t - 3.5)
        hi = bisect.bisect_right(self._heart_t0, t)
        for heart in self._hearts[lo:hi]:
            a = (t - heart.t0) / heart.life
            if not 0 <= a < 1:
                continue
            y = y0 - heart.rise * (1 - (1 - a) ** 2)
            x = x0 + heart.amp * math.sin(heart.phase + a * heart.freq * math.tau) + heart.drift * a
            pop = min(1.0, a / 0.07)
            size = max(2, round(self._heart_px * heart.size * (0.4 + 0.6 * pop)))
            alpha = 1.0 if a < 0.5 else (1 - a) / 0.5
            comp(img, tint(shape_mask("heart", size), heart.color), x - size / 2, y - size / 2, alpha * 0.95)

    # ----------------------------------------------------------------- desktop layout

    def _setup_desktop(self, avatar):
        cfg, W, H = self.cfg, self.W, self.H
        u = self.u = H / 720 * cfg.ui_scale
        self.fs = max(8, round(13.5 * u))
        self.anim, self.gap = 0.12, 0
        pw = int(round(340 * u / 2) * 2)
        if cfg.panel_mode == "overlay":
            pw = min(pw, int(W * 0.45 / 2) * 2)
            self.out_size, px, video_w = (W, H), W - pw, W - pw
        else:
            self.out_size, px, video_w = (W + pw, H), W, W
        self._panel = (px, pw)
        base = Image.new("RGBA", self.out_size, (0, 0, 0, 0))
        line = max(1, round(u))
        pad = round(16 * u)

        # --- side panel: header, message area, input footer
        panel = Image.new("RGBA", (pw, H), PANEL_BG + (255,))
        draw = ImageDraw.Draw(panel)
        head_h, foot_h = round(50 * u), round(104 * u)
        draw.rectangle((0, 0, line - 1, H), fill=PANEL_LINE)
        draw.rectangle((0, head_h, pw, head_h + line - 1), fill=PANEL_LINE)
        draw.rectangle((0, H - foot_h, pw, H - foot_h + line - 1), fill=PANEL_LINE)
        draw_text(panel, pad, head_h / 2, "Live chat", self.fonts.get(round(14.5 * u), True),
                  PANEL_TEXT + (255,), "lm")
        field_y, field_h = H - foot_h + round(12 * u), round(40 * u)
        field_w = pw - 2 * round(12 * u)
        comp(panel, tint(rounded_mask(field_w, field_h, 6 * u), PANEL_FIELD), round(12 * u), field_y)
        draw_text(panel, round(24 * u), field_y + field_h / 2, "Send a message",
                  self.fonts.get(self.fs), PANEL_DIM + (255,), "lm")
        f_btn = self.fonts.get(round(13 * u), True)
        btn_w, btn_h = round(f_btn.getlength("Chat") + 26 * u), round(30 * u)
        btn_x, btn_y = pw - round(12 * u) - btn_w, field_y + field_h + round(10 * u)
        comp(panel, tint(rounded_mask(btn_w, btn_h, 6 * u), self.accent), btn_x, btn_y)
        draw_text(panel, btn_x + btn_w / 2, btn_y + btn_h / 2, "Chat", f_btn, self.on_accent, "mm")
        comp(base, panel, px, 0)

        self.chat_x, self.chat_y = px + line, head_h + line + round(4 * u)
        self.chat_w, self.chat_h = pw - line, H - foot_h - self.chat_y - round(4 * u)
        self._header = (px + pw - pad, head_h / 2)

        # --- on the video: LIVE badge + uptime (top-left), stream info (bottom-left)
        f_small = self.fonts.get(round(12.5 * u), True)
        bh = round(26 * u)
        bx, by = round(16 * u), round(16 * u)
        self._scrim(base, 0, 0, video_w, round(H * 0.16), 0.4, dark_at_top=True)
        live_w = round(f_small.getlength("LIVE") + 18 * u)
        comp(base, tint(rounded_mask(live_w, bh, 5 * u), LIVE_RED), bx, by)
        draw_text(base, bx + live_w / 2, by + bh / 2, "LIVE", f_small, WHITE, "mm")
        up_w = round(f_small.getlength("8:88:88") + 16 * u)
        self._uptime_box = (bx + live_w + round(6 * u), by, up_w, bh)

        if cfg.streamer_name or cfg.stream_title:
            self._scrim(base, 0, H - round(H * 0.3), video_w, round(H * 0.3), 0.72, dark_at_top=False)
            av = round(46 * u)
            ax, ay = round(18 * u), H - round(18 * u) - av
            comp(base, self._avatar(avatar, av, cfg.streamer_name), ax, ay)
            tx, text_max = ax + av + round(12 * u), video_w - (ax + av + round(12 * u)) - pad
            f_name, f_title = self.fonts.get(round(16 * u), True), self.fonts.get(round(13.5 * u))
            name = ellipsize(cfg.streamer_name, f_name, text_max)
            title = ellipsize(cfg.stream_title, f_title, text_max)
            if name and title:
                draw_text(base, tx, ay + av * 0.42, name, f_name, WHITE)
                draw_text(base, tx, ay + av * 0.86, title, f_title, (255, 255, 255, 215))
            else:
                draw_text(base, tx, ay + av / 2, name or title, f_name, WHITE, "lm")
        self._static = base

    def _draw_desktop_dynamic(self, img, t: float):
        u = self.u
        # viewer count, right-aligned in the panel header
        text = f"{self.viewers_at(t):,}"
        key = ("header", text)
        if key not in self._small:
            font = self.fonts.get(round(13 * u), True)
            icon = round(14 * u)
            w = icon + round(5 * u) + math.ceil(font.getlength(text)) + 2
            sprite = Image.new("RGBA", (w, round(22 * u)), (0, 0, 0, 0))
            comp(sprite, tint(shape_mask("person", icon), LIVE_RED), 0, (sprite.height - icon) // 2)
            draw_text(sprite, icon + round(5 * u), sprite.height / 2, text, font, LIVE_RED + (255,), "lm")
            self._small[key] = sprite
        sprite = self._small[key]
        right, mid = self._header
        comp(img, sprite, right - sprite.width, mid - sprite.height / 2)

        # stream uptime next to the LIVE badge
        total = int(self.cfg.uptime_minutes * 60 + t)
        hours, minutes, seconds = total // 3600, total % 3600 // 60, total % 60
        clock = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        key = ("uptime", clock)
        if key not in self._small:
            x, y, w, h = self._uptime_box
            pill = tint(rounded_mask(w, h, 5 * u), (0, 0, 0), 150)
            draw_text(pill, w / 2, h / 2, clock, self.fonts.get(round(12.5 * u), True), WHITE, "mm")
            if len(self._small) > 4000:
                self._small.clear()
            self._small[key] = pill
        comp(img, self._small[key], self._uptime_box[0], self._uptime_box[1])

    def _sprite_desktop(self, ev) -> Image.Image:
        u, fs = self.u, self.fs
        f, fb = self.fonts.get(fs), self.fonts.get(fs, True)
        pad_x, pad_y, line_h = round(16 * u), round(5 * u), round(fs * 1.5)
        max_w = self.chat_w - 2 * pad_x
        if ev.kind == "tip":
            card = self._tip_card(ev, self.chat_w - 2 * round(10 * u))
            sprite = Image.new("RGBA", (self.chat_w, card.height + 2 * pad_y), PANEL_BG + (255,))
            comp(sprite, card, round(10 * u), pad_y)
            return sprite
        if ev.kind in ("join", "follow", "system"):
            dim = PANEL_DIM + (255,)
            words = self._words(ev.user, fb, dim, fs) if ev.user else []
            words += self._words(self._notice_text(ev), f, dim, fs)
        else:
            words = self._badge_words(ev, fs)
            name = self._words(ev.user, fb, self._name_fill(ev), fs)
            if name:
                name[-1].append(Atom(w=f.getlength(":"), text=":", font=f, fill=PANEL_TEXT + (255,)))
            words += name + self._words(ev.text, f, PANEL_TEXT + (255,), fs)
        lines = flow(words, max_w, f.getlength(" "))
        sprite = Image.new("RGBA", (self.chat_w, len(lines) * line_h + 2 * pad_y), PANEL_BG + (255,))
        self._draw_lines(sprite, lines, pad_x, pad_y, line_h, f)
        return sprite
