"""ComfyUI nodes: composite a livestream UI (chat, viewer count, reactions) onto video.

Two nodes share one implementation:

* ``LivestreamOverlayVideo`` - VIDEO in, VIDEO out. Frame rate and audio come from the video.
* ``LivestreamOverlay``      - IMAGE batch in, IMAGE batch out, with an explicit fps.
"""
from __future__ import annotations

import os

import numpy as np
import torch
from PIL import Image

from .chat_parser import parse_comments
from .renderer import OverlayConfig, OverlayRenderer

try:
    from comfy.utils import ProgressBar
except Exception:  # running outside ComfyUI
    ProgressBar = None

DEFAULT_COMMENTS = """\
# One comment per line: "time user: message". Timestamps are optional.
# SRT and JSON also work - see the README.
0:01 pixel_otter: first!! 🔥
0:02 maplefan22: [join]
0:03 nightowl[mod]: welcome in everyone
0:05 sam_k: where are you streaming from today?
0:07 LunaBee[sub]: this looks amazing 😍
0:09 quietmango: [tip $5] coffee is on me ☕
0:11 turbo_yeti: can you show that again?
0:13 dana.w: [follow]
"""

DESCRIPTION = ("Draws a livestream interface over a video: scrolling chat driven by a timed "
               "comment script, LIVE badge, viewer count and floating reactions.")


def _option_inputs():
    """Widgets shared by both nodes, in display order."""
    return {
        "comments": ("STRING", {"multiline": True, "default": DEFAULT_COMMENTS,
                                "tooltip": "Comment script: SRT, chat-log lines or JSON."}),
        "comment_format": (["auto", "lines", "srt", "json"],),
        "layout": (["mobile", "desktop"],
                   {"tooltip": "mobile: chat floats over the video. desktop: chat in a side panel."}),
        "desktop_panel": (["extend canvas", "overlay video"],
                          {"tooltip": "Desktop layout only. 'extend canvas' makes the output wider."}),
        "streamer_name": ("STRING", {"default": "streamer"}),
        "stream_title": ("STRING", {"default": ""}),
        "viewer_count": ("INT", {"default": 1280, "min": 1, "max": 50_000_000}),
        "accent_color": ("STRING", {"default": "#6d5efc",
                                    "tooltip": "Hex colour for buttons, sub badges and tip cards."}),
        "reaction_rate": ("FLOAT", {"default": 2.0, "min": 0.0, "max": 30.0, "step": 0.1,
                                    "tooltip": "Floating hearts per second (mobile layout). 0 turns them off."}),
        "ui_scale": ("FLOAT", {"default": 1.0, "min": 0.4, "max": 3.0, "step": 0.05}),
        "start_time": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 86400.0, "step": 0.01,
                                 "tooltip": "Timeline position of the first frame. Use it when rendering a long video in chunks."}),
        "uptime_minutes": ("FLOAT", {"default": 47.0, "min": 0.0, "max": 6000.0, "step": 1.0,
                                     "tooltip": "How long the stream has already been live (desktop layout clock)."}),
        "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
    }


def _optional_inputs():
    return {
        "avatar": ("IMAGE", {"tooltip": "Streamer profile picture (first image of the batch is used)."}),
        "comments_file": ("STRING", {"default": "",
                                     "tooltip": "Optional path to a .srt / .txt / .json file. Overrides the text box."}),
        "chat_bg_opacity": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                                      "tooltip": "Mobile layout: darkness of the bubble behind each message. 0 = no background (text gets a soft shadow instead), 0.45 = classic dark bubble."}),
    }


def render_overlay(images, fps, comments, comment_format, layout, desktop_panel, streamer_name,
                   stream_title, viewer_count, accent_color, reaction_rate, ui_scale, start_time,
                   uptime_minutes, seed, avatar=None, comments_file="", chat_bg_opacity=0.0):
    """Draw the overlay on an IMAGE batch ([N, H, W, C] floats in 0..1) and return a new batch."""
    path = (comments_file or "").strip().strip('"').strip("'")
    if path:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Livestream Overlay: comments_file not found: {path}")
        with open(path, "r", encoding="utf-8-sig") as fh:
            comments = fh.read()
    events = parse_comments(comments, comment_format, seed)

    count, height, width = images.shape[0], images.shape[1], images.shape[2]
    avatar_img = None
    if avatar is not None:
        pixels = (avatar[0, ..., :3].cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
        avatar_img = Image.fromarray(pixels)

    config = OverlayConfig(
        layout=layout,
        panel_mode="overlay" if desktop_panel.startswith("overlay") else "extend",
        streamer_name=streamer_name.strip(),
        stream_title=stream_title.strip(),
        viewer_count=viewer_count,
        accent=accent_color,
        reaction_rate=reaction_rate,
        ui_scale=ui_scale,
        chat_bg_opacity=chat_bg_opacity,
        uptime_minutes=uptime_minutes,
        seed=seed,
    )
    renderer = OverlayRenderer(width, height, events, config,
                               end_time=start_time + count / fps, avatar=avatar_img)
    out_w, out_h = renderer.out_size
    out = torch.empty((count, out_h, out_w, 3), dtype=torch.float32)
    progress = ProgressBar(count) if ProgressBar else None
    for i in range(count):
        frame = images[i].cpu().numpy()
        out[i] = torch.from_numpy(renderer.composite(frame, start_time + i / fps))
        if progress:
            progress.update(1)
    return out


def _video_from_frames(images, audio, frame_rate):
    """Wrap frames (+ optional audio) as a ComfyUI VIDEO. Imported lazily so the image
    node still loads on ComfyUI builds that predate the VIDEO type."""
    try:
        from comfy_api.latest import InputImpl, Types
        make, components = InputImpl.VideoFromComponents, Types.VideoComponents
    except ImportError:
        try:
            from comfy_api.input_impl import VideoFromComponents as make
            from comfy_api.util import VideoComponents as components
        except ImportError as exc:
            raise RuntimeError(
                "Livestream Overlay (Video) needs a ComfyUI version with the VIDEO type. "
                "Update ComfyUI, or use the Images node with Load Video / Video Combine nodes."
            ) from exc
    return make(components(images=images, audio=audio, frame_rate=frame_rate))


class LivestreamOverlayVideo:
    CATEGORY = "video/livestream"
    FUNCTION = "apply"
    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("video",)
    DESCRIPTION = DESCRIPTION + " Takes a video and returns a video; frame rate and audio are kept."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"video": ("VIDEO", {"tooltip": "The video to draw on. Its frame rate and audio are kept."}),
                         **_option_inputs()},
            "optional": _optional_inputs(),
        }

    def apply(self, video, **options):
        source = video.get_components()
        frames = render_overlay(source.images, float(source.frame_rate), **options)
        return (_video_from_frames(frames, source.audio, source.frame_rate),)


class LivestreamOverlay:
    CATEGORY = "video/livestream"
    FUNCTION = "apply"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    DESCRIPTION = DESCRIPTION + " Takes and returns a batch of frames."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.01,
                                  "tooltip": "Frame rate of the video; frame i is shown at start_time + i / fps."}),
                **_option_inputs(),
            },
            "optional": _optional_inputs(),
        }

    def apply(self, images, fps, **options):
        return (render_overlay(images, fps, **options),)


NODE_CLASS_MAPPINGS = {
    "LivestreamOverlayVideo": LivestreamOverlayVideo,
    "LivestreamOverlay": LivestreamOverlay,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "LivestreamOverlayVideo": "Livestream Overlay (Video)",
    "LivestreamOverlay": "Livestream Overlay (Images)",
}
