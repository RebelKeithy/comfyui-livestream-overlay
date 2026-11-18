# Agent guide: writing a chat script for the Livestream Overlay node

You are writing the viewer chat for a simulated livestream. Your output is pasted
into the `comments` input of a ComfyUI node that draws it over a video, so it must
parse exactly. Write nothing but the script: no introduction, no code fence.

## What you need first

Ask for anything missing that you can't reasonably infer:

- Video length in seconds.
- What happens on screen and when (a rough timeline is enough).
- Who the streamer is, what kind of stream it is, and the tone of the audience.
- Approximate viewer count, and the language the audience writes in.

## Output format

One comment per line, with a timestamp on every line:

```
m:ss.s username: message
```

- **Time** is video time: `0:07.4` is 7.4 s after the first frame. Stay within the
  video length. Negative times (`-0:05.0`) are comments already in the chat when the
  video starts.
- **Username**: 3-20 characters, no spaces, none of `: [ ] |`. The colon comes
  directly after it, then a space.
- **Message**: a single line. No line breaks.
- Lines starting with `#` are ignored by the node. Use them only for the header below.

Special lines:

| Write | Result |
|---|---|
| `0:03.0 nightowl[mod]: be nice` | Badge. One of `mod`, `sub`, `vip`, `verified`; combine as `[mod,sub]`. |
| `0:04.2 maplefan22: [join]` | "maplefan22 joined" |
| `0:06.9 dana.w: [follow]` | "dana.w followed" |
| `0:09.0 quietmango: [tip $5] love this` | Highlighted tip. The message is optional. |
| `0:12.0 [system] Slow mode is on.` | System notice, no username. |

Start the script with a header suggesting the node's other settings:

```
# streamer_name: maya.makes
# stream_title: Sunday studio hangout
# viewer_count: 1300
```

## Rules that keep it parseable

1. Every line is a header line or starts with a timestamp. No blank-line tricks, no
   bullets, no numbering.
2. Give every comment a username. Without one, the first word is taken as the
   username whenever it ends in a colon.
3. Badges are not remembered. Repeat `[mod]` every time that user speaks.
4. Use plain single emoji (🔥 😂 👍 ❤). Avoid flags, skin tones and joined emoji
   such as family or profession sequences; they may not render.
5. Keep lines in time order.

## Making it read as a real chat

- **Open mid-conversation.** Put 4-6 comments at negative times so the first frame
  shows a chat already going. An empty chat on a stream that is "live" looks staged.
- **Be bursty.** Cluster 3-6 comments within a second or two after something happens,
  then leave a gap. Never space comments evenly.
- **React late.** Viewers respond 1-3 s after an on-screen moment, never at the same
  instant and never before it.
- **Match volume to audience.** As a rule of thumb: about 50 viewers, one comment
  every 5-10 s; about 500, one every 1-2 s; several thousand, 2-3 per second. Do not
  go above 3 per second, since nobody could read it.
- **Reuse people.** Build a cast of roughly 8-15 usernames for a short clip and bring
  them back. Give one or two a consistent voice (the regular, the one asking
  questions, the mod).
- **Keep most messages short.** Mostly 1-6 words, lowercase, loose punctuation, the
  odd typo. Include an occasional longer question, kept under about 100 characters.
- **Vary what people do.** Reactions, questions to the streamer, replies to each other
  (`@name`), an off-topic remark, someone arriving late and asking what they missed.
- **Let several people say nearly the same thing** after a big moment. That overlap
  is what real chat looks like.
- **Use events sparingly.** A few joins and follows, at most one tip per 20-30 s
  unless told otherwise, and system lines only when there is a reason.
- **Don't narrate the video.** Viewers react ("WAIT", "no way", "how??"); they don't
  describe what is plainly on screen.
- **Invent the usernames.** Do not use the handles of real people or brands.

## Example

Brief: 12 s clip, a painter reveals a finished canvas at 0:05, about 1,300 viewers,
relaxed art stream.

```
# streamer_name: maya.makes
# stream_title: Sunday studio hangout
# viewer_count: 1300
-0:06.5 inkfinch: the blue is doing so much work here
-0:05.1 nightowl[mod]: reminder: commissions open friday
-0:03.8 pebble_jo: just got here what did i miss
-0:02.2 inkfinch: @pebble_jo shes about to show the whole thing
-0:00.9 tall_fern: turn it around turn it around
0:01.3 moss.and.co: [join]
0:02.0 pebble_jo: ok im ready
0:03.4 quietmango: the suspense
0:06.1 tall_fern: OH
0:06.4 inkfinch: WAIT
0:06.6 pebble_jo: no way
0:06.9 sodapop_lin[sub]: that is so good 😭
0:07.3 quietmango: how is that the same painting from an hour ago
0:07.8 tall_fern: the light on the water??
0:08.5 ravi_draws: [follow]
0:09.2 sodapop_lin[sub]: [tip $5] for the paint fund
0:10.4 nightowl[mod]: told you all it would come together
0:11.2 pebble_jo: what brush did you use for the clouds
```

## Check before you answer

- Every non-header line starts with a timestamp and has `username: ` after it.
- No timestamp is later than the video length.
- Reactions come after the moments they react to.
- Nothing but the script is in your reply.
