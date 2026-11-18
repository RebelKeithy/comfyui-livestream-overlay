# ComfyUI Livestream Overlay

ComfyUI custom nodes that composite a livestream interface onto a video: a scrolling chat driven by a timed comment script, a LIVE badge, a drifting
viewer count, floating heart reactions, and join / follow / tip events.

The overlay is drawn in code (Pillow), so text is always sharp and legible and the
timing is exact - none of it goes through a diffusion model.

The interface is a generic, original design. It follows the conventions people know
from stream apps but does not copy any particular platform's branding.

## Install

From ComfyUI's `custom_nodes` folder:

```
git clone https://github.com/RebelKeithy/comfyui-livestream-overlay.git
```

Then restart ComfyUI. To update later, run `git pull` inside the
`comfyui-livestream-overlay` folder and restart again.

ComfyUI-Manager's "Install via Git URL" works with the same address.

There is nothing to `pip install`: it only needs Pillow, numpy and torch, which
ComfyUI already ships with. Pillow 10.1 or newer is recommended.

## Nodes

Both are under `video/livestream` and have the same settings.

**Livestream Overlay (Video)** takes a `VIDEO` and returns a `VIDEO`. The frame rate
and audio track come from the input and are passed through, so there is no `fps` to
set:

```
Load Video ──► Livestream Overlay (Video) ──► Save Video
```

It needs a ComfyUI version that has the `VIDEO` type (the one Load Video, Create Video
and Save Video use). The output is a standard 8-bit sRGB video.

**Livestream Overlay (Images)** takes and returns an `IMAGE` batch, for workflows that
pass frames around (a sampler's decoded output, or VideoHelperSuite's Load Video and
Video Combine):

```
frames (IMAGE batch) ──► Livestream Overlay (Images) ──► Video Combine
```

Set its `fps` to the frame rate you give your video-combine node.

In both, frame `i` is drawn at `start_time + i / fps` seconds on the comment timeline.

## Inputs

| Input | What it does |
|---|---|
| `video` | Video node only: the video to draw on. |
| `images`, `fps` | Images node only: the frames to draw on and their frame rate. |
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

- The parts of the interface that never change (scrims, pills, the panel) are blended
  into the whole batch in one pass; per frame only the small areas that move (chat,
  counters, hearts) are redrawn. Most of the remaining time is writing the output
  frames. Memory use is the input batch plus the output batch.
- The Video node decodes the whole clip into memory first, as ComfyUI's own video
  nodes do, so very long videos are better handled in chunks with `start_time`.
- `extend canvas` changes the output width (by 340 px at 720p, scaled with height and
  `ui_scale`). The panel width is always even, so the result stays encoder-friendly
  if your source width is.
- `renderer.py` and `chat_parser.py` have no ComfyUI or torch imports, so they can be
  reused in other scripts.
