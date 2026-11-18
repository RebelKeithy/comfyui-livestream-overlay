"""Render the overlay without ComfyUI - handy for tuning a comment script.

    python preview.py --comments examples/chat.srt --layout mobile --size 720x1280 --out preview.mp4
    python preview.py --comments examples/chat.txt --layout desktop --size 1280x720 --frame-at 9 --out still.png

``--background`` takes an image to use instead of the generated gradient.
An .mp4 output needs ffmpeg on PATH; a .png output writes the single frame at --frame-at.
"""
import argparse
import os
import subprocess
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chat_parser import parse_comments  # noqa: E402
from renderer import OverlayConfig, OverlayRenderer  # noqa: E402


def background(width, height, t, picture=None):
    if picture is not None:
        return picture
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    cx, cy = width * (0.5 + 0.2 * np.sin(t * 0.6)), height * (0.42 + 0.05 * np.cos(t * 0.9))
    glow = np.exp(-(((xx - cx) / (width * 0.45)) ** 2 + ((yy - cy) / (height * 0.35)) ** 2))
    top, bottom = np.array([0.20, 0.24, 0.36]), np.array([0.52, 0.36, 0.30])
    ramp = (yy / height)[..., None]
    frame = top * (1 - ramp) + bottom * ramp + glow[..., None] * np.array([0.35, 0.30, 0.22])
    return np.clip(frame, 0, 1).astype(np.float32)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comments", required=True)
    ap.add_argument("--format", default="auto", choices=["auto", "lines", "srt", "json"])
    ap.add_argument("--layout", default="mobile", choices=["mobile", "desktop"])
    ap.add_argument("--panel", default="extend", choices=["extend", "overlay"])
    ap.add_argument("--size", default="720x1280")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--seconds", type=float, default=12)
    ap.add_argument("--frame-at", type=float, default=None, help="time of the still for .png output")
    ap.add_argument("--streamer", default="streamer")
    ap.add_argument("--title", default="")
    ap.add_argument("--viewers", type=int, default=1280)
    ap.add_argument("--accent", default="#6d5efc")
    ap.add_argument("--reactions", type=float, default=2.0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--chat-bg", type=float, default=0.0, help="mobile bubble opacity, 0..1")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--background", default=None)
    ap.add_argument("--out", default="preview.mp4")
    args = ap.parse_args()

    width, height = (int(v) for v in args.size.lower().split("x"))
    with open(args.comments, encoding="utf-8-sig") as fh:
        events = parse_comments(fh.read(), args.format, args.seed)
    cfg = OverlayConfig(layout=args.layout, panel_mode=args.panel, streamer_name=args.streamer,
                        stream_title=args.title, viewer_count=args.viewers, accent=args.accent,
                        reaction_rate=args.reactions, ui_scale=args.scale,
                        chat_bg_opacity=args.chat_bg, seed=args.seed)
    renderer = OverlayRenderer(width, height, events, cfg, end_time=args.seconds)
    picture = None
    if args.background:
        img = Image.open(args.background).convert("RGB").resize((width, height), Image.LANCZOS)
        picture = np.asarray(img, dtype=np.float32) / 255.0

    def frame_at(t):
        out = renderer.composite(background(width, height, t, picture), t)
        return (out * 255.0 + 0.5).clip(0, 255).astype(np.uint8)

    if args.out.lower().endswith(".png"):
        t = args.seconds if args.frame_at is None else args.frame_at
        Image.fromarray(frame_at(t)).save(args.out)
        print(f"wrote {args.out} ({len(events)} events, frame at {t:.2f}s)")
        return

    out_w, out_h = renderer.out_size
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{out_w}x{out_h}", "-r", str(args.fps), "-i", "-",
         "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p", args.out],
        stdin=subprocess.PIPE)
    total = int(args.seconds * args.fps)
    for i in range(total):
        ffmpeg.stdin.write(frame_at(i / args.fps).tobytes())
    ffmpeg.stdin.close()
    ffmpeg.wait()
    print(f"wrote {args.out} ({total} frames, {len(events)} events)")


if __name__ == "__main__":
    main()
