# ComfyUI Livestream Overlay

A ComfyUI custom node that composites a livestream interface onto a batch of video
frames: a scrolling chat driven by a timed comment script, a LIVE badge, a drifting
viewer count, floating heart reactions, and join / follow / tip events.

The overlay is drawn in code (Pillow), so text is always sharp and legible and the
timing is exact - none of it goes through a diffusion model.

The interface is a generic, original design. It follows the conventions people know
from stream apps but does not copy any particular platform's branding.

## Install

1. Copy this folder into `ComfyUI/custom_nodes/` (so you have
   `ComfyUI/custom_nodes/comfyui-livestream-overlay/__init__.py`).
2. Restart ComfyUI.

There is nothing to `pip install`: it only needs Pillow, numpy and torch, which
ComfyUI already ships with. Pillow 10.1 or newer is recommended.

The node appears as **Livestream Overlay (chat + UI)** under `video/livestream`.

## Wiring

```
your frames (IMAGE batch) ──► Livestream Overlay ──► Video Combine / Save
```

Set `fps` to the same frame rate you pass to your video-combine node. Frame `i` is
drawn at `start_time + i / fps` seconds on the comment timeline.

## Inputs

| Input | What it does |
|---|---|
| `images` | Frames to draw on. |
| `fps` | Frame rate of the video. |
| `comments` | The comment script (see formats below). |
| `comment_format` | `auto` detects JSON, then SRT, otherwise chat-log lines. |
| `layout` | `mobile`: chat floats over the bottom-left of the video, with hearts. `desktop`: chat in a side panel. |
| `desktop_panel` | `extend canvas` adds the panel to the right (output gets wider). `overlay video` draws it over the right edge. |
| `streamer_name`, `stream_title` | Shown in the streamer pill (mobile) or bottom-left of the video (desktop). |
| `viewer_count` | Starting viewer count. It wanders slowly around this value and updates every 3 s. |
| `accent_color` | Hex colour for the Follow / Chat buttons, sub badges and tip cards. |
| `reaction_rate` | Floating hearts per second in the mobile layout. `0` turns them off. Tips and follows add a small burst. |
| `ui_scale` | Scales the whole interface (0.4-3.0): text, avatars, pills, panel width. Raise it if the video will be watched small. |
| `start_time` | Timeline position of the first frame. For rendering a long video in chunks: chunk 2 of a 10 s-per-chunk job uses `10`. |
| `uptime_minutes` | How long the stream has "already been live" (the clock in the desktop layout). |
| `seed` | Controls generated usernames, auto-timing jitter, hearts and viewer drift. Same seed = same result, including across chunks. |
| `avatar` (optional) | Streamer profile picture. Without it, a coloured circle with the initial is used. |
| `comments_file` (optional) | Path to a `.srt` / `.txt` / `.json` file. Overrides the text box when set. |
| `chat_bg_opacity` (optional) | Mobile layout: how dark the bubble behind each message is. `0` (default) is text only with a soft shadow for legibility; `0.45` gives a dark translucent bubble; `1` is solid. Tip cards keep their accent colour. |

## Comment formats

In the two text formats a comment is `username: message`. If there is no
`username:` prefix, a random username is generated.

### Chat-log lines (simplest)

```
0:01 pixel_otter: first!! 🔥
[0:03] nightowl[mod]: welcome in everyone
0:05.5 | sam_k | where are you streaming from?
7.2 | LunaBee[sub] | this looks amazing
turbo_yeti: no timestamp - placed ~1.6 s after the previous comment
lol same
# lines starting with # are ignored
```

Timestamps can be `m:ss`, `h:mm:ss.mmm`, `[m:ss]`, or plain seconds followed by `|`.
A negative timestamp (`-0:04 user: hi`) puts the comment in the chat before the first
frame, so the video opens on a chat that is already in progress.
Leave them out entirely and the comments are spaced automatically with a slightly
irregular rhythm.

### SRT

Standard SubRip. The cue **start** time is when the comment appears (the end time is
ignored - comments scroll away on their own).

```
1
00:00:02,100 --> 00:00:06,000
nightowl[mod]: welcome in everyone
```

### Events and badges (both text formats)

| Write | Result |
|---|---|
| `user[mod]: hi` | Badge before the name. Available: `mod`, `sub`, `vip`, `verified`. Combine with commas. |
| `user: [join]` | "user joined" notice |
| `user: [follow]` | "user followed" notice |
| `user: [tip $5] thanks!` | Highlighted tip card in the accent colour |
| `[system] Slow mode is on.` | Neutral system line |

### JSON (full control)

```json
[
  {"time": 2.1, "user": "nightowl", "badges": ["mod"], "text": "welcome in"},
  {"time": "0:03.3", "user": "sam_k", "text": "hello", "color": "#ffd166"},
  {"time": 5.0, "user": "quietmango", "type": "tip", "amount": "$5.00", "text": "coffee is on me"},
  {"time": 6.9, "user": "dana.w", "type": "follow"}
]
```

Keys: `time` (seconds or a `m:ss` string), `user`, `text`, `type`
(`chat`, `join`, `follow`, `tip`, `system`), `color`, `badges`, `amount`.

Working examples of all three are in `examples/`. To have an LLM write the script for
you, give it `AGENT_GUIDE.md` together with a description of your video.

## Making it look believable

- **Pacing matters more than styling.** Real chat is bursty: three comments in a
  second after something happens, then a lull. Evenly spaced comments look scripted.
- **Let people repeat.** A handful of usernames showing up several times reads as a
  real audience; 40 unique one-off names does not.
- **React to the video.** Time a few comments 1-2 s after visible moments.
- **Mix lengths.** Mostly short ("lol", an emoji), with the occasional long question.
- Match `viewer_count` to the chat speed - 50 viewers do not produce 5 comments a second.

## Fonts and emoji

The node looks for Inter, Roboto, Segoe UI, Helvetica, Arial, Noto Sans, Liberation
Sans or DejaVu Sans on the system, and for a colour emoji font (Segoe UI Emoji, Apple
Color Emoji, Noto Color Emoji).

To force specific fonts, drop them into a `fonts/` folder inside this node: any file
with `bold` in its name becomes the bold face, one with `emoji` the emoji font, and
the remaining one the regular face.

If no emoji font is found, emoji are left out of the text. Flags and joined emoji
(family, profession sequences) need Pillow built with Raqm; otherwise they show as
their separate parts.

## Preview without ComfyUI

`preview.py` renders the overlay on a placeholder background so you can iterate on a
script quickly:

```
python preview.py --comments examples/chat.srt --layout mobile --size 720x1280 --out preview.mp4
python preview.py --comments examples/chat.txt --layout desktop --size 1280x720 --frame-at 9 --out still.png
```

## Notes

- Speed is roughly 40-60 ms per 1080p frame on CPU. Frames are processed one at a
  time, so memory use is the input batch plus the output batch.
- `extend canvas` changes the output width (by 340 px at 720p, scaled with height and
  `ui_scale`). The panel width is always even, so the result stays encoder-friendly
  if your source width is.
- `renderer.py` and `chat_parser.py` have no ComfyUI or torch imports, so they can be
  reused in other scripts.
