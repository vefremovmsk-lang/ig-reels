#!/usr/bin/env python3
"""Render a vertical 1080x1920 Reel from a script JSON.

Pipeline (all free, no API keys, no card):
  edge-tts  -> voiceover MP3 + per-word timings
  timings   -> styled ASS subtitles (word chunks) + top hook + handle
  ffmpeg    -> animated gradient background + burned subtitles + audio -> MP4

Usage:
  python scripts/render.py <script.json> <out.mp4>

Script JSON:
  {
    "hook": "Прибыль есть, а денег нет?",
    "voiceover": "...полный текст озвучки...",
    "caption": "...текст подписи с #хэштегами..."
  }

Writes <out.mp4> and a sibling caption.txt.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import edge_tts

VOICE = os.environ.get("TTS_VOICE", "ru-RU-DmitryNeural")
FONT_NAME = os.environ.get("SUB_FONT_NAME", "Noto Sans")
W, H = 1080, 1920


def ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


async def _synth_once(text: str, mp3_path: str):
    """One synthesis attempt. Returns timing segments [start, end, text].

    edge-tts may emit WordBoundary (one word) or SentenceBoundary (a phrase),
    depending on version. We accept any *Boundary event and normalize later.
    Audio is buffered and written only on success (no partial files).
    """
    comm = edge_tts.Communicate(text, VOICE)
    segments = []
    audio = bytearray()
    async for ch in comm.stream():
        ctype = ch.get("type", "")
        if ctype == "audio":
            audio.extend(ch["data"])
        elif ctype.endswith("Boundary"):
            start = ch["offset"] / 1e7              # 100ns ticks -> seconds
            end = start + ch["duration"] / 1e7
            segments.append([start, end, ch["text"]])
    if not audio:
        raise RuntimeError("edge-tts returned no audio")
    with open(mp3_path, "wb") as f:
        f.write(audio)
    return segments


async def synth(text: str, mp3_path: str, retries: int = 5):
    """Synthesize with retries — edge-tts throws NoAudioReceived intermittently."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            return await _synth_once(text, mp3_path)
        except Exception as e:
            last = e
            print(f"  edge-tts attempt {attempt}/{retries} failed: {e}")
            await asyncio.sleep(3 * attempt)
    raise RuntimeError(f"edge-tts failed after {retries} attempts: {last}")


def expand_to_words(segments):
    """Split multi-word segments into per-word timings (char-weighted)."""
    words = []
    for start, end, text in segments:
        toks = text.split()
        if not toks:
            continue
        if len(toks) == 1:
            words.append([start, end, toks[0]])
            continue
        span = max(end - start, 0.01)
        weights = [len(w) + 1 for w in toks]
        total = sum(weights)
        t = start
        for w, wt in zip(toks, weights):
            d = span * wt / total
            words.append([t, t + d, w])
            t += d
    return words


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def chunk_words(words, max_words=3, max_gap=0.55):
    """Group words into short on-screen caption chunks."""
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w[0] - cur[-1][1] > max_gap):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def esc(text: str) -> str:
    """Neutralize ASS control characters in plain text."""
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(chunks, hook, duration, ass_path, handle="@ai_findir"):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{FONT_NAME},104,&H00FFFFFF,&H000000FF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,7,4,5,90,90,0,204
Style: Hook,{FONT_NAME},66,&H0000FFFF,&H000000FF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,6,3,8,80,80,170,204
Style: Tag,{FONT_NAME},40,&H00FFFFFF,&H000000FF,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,3,2,2,60,60,80,204

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Hook,,0,0,0,,{esc(hook)}")
    ev.append(f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Tag,,0,0,0,,{esc(handle)}")
    for ch in chunks:
        start = ass_time(ch[0][0])
        end = ass_time(ch[-1][1])
        text = esc(" ".join(w[2] for w in ch).strip().upper())
        ev.append(f"Dialogue: 0,{start},{end},Sub,,0,0,0,,{text}")
    Path(ass_path).write_text(header + "\n".join(ev) + "\n", encoding="utf-8")


def render(mp3, ass, duration, out_mp4):
    bg = (f"gradients=s={W}x{H}:c0=0x0a1830:c1=0x143a72:c2=0x0a1830:"
          f"x0=140:y0=260:x1=940:y1=1680:nb_colors=3:speed=0.005:"
          f"duration={duration + 0.3:.2f}:rate=30")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", bg,
        "-i", mp3,
        "-vf", f"subtitles={ass}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        out_mp4,
    ]
    subprocess.run(cmd, check=True)


def main():
    if len(sys.argv) != 3:
        print("usage: render.py <script.json> <out.mp4>", file=sys.stderr)
        sys.exit(2)
    script_path, out_mp4 = sys.argv[1], sys.argv[2]
    data = json.loads(Path(script_path).read_text(encoding="utf-8"))
    voiceover = data["voiceover"].strip()
    hook = data.get("hook", "").strip()

    tmp = tempfile.mkdtemp()
    mp3 = os.path.join(tmp, "vo.mp3")
    segments = asyncio.run(synth(voiceover, mp3))
    words = expand_to_words(segments)
    duration = probe_duration(mp3)

    if not words:  # fallback: even split if the voice emitted no boundaries at all
        toks = voiceover.split()
        per = duration / max(len(toks), 1)
        words = [[i * per, (i + 1) * per, t] for i, t in enumerate(toks)]

    chunks = chunk_words(words)
    ass = os.path.join(tmp, "sub.ass")
    build_ass(chunks, hook, duration, ass)

    Path(out_mp4).parent.mkdir(parents=True, exist_ok=True)
    render(mp3, ass, duration, out_mp4)

    caption = data.get("caption", "").strip()
    Path(out_mp4).with_name("caption.txt").write_text(caption, encoding="utf-8")
    print(f"Rendered {out_mp4}  ({duration:.1f}s, {len(chunks)} subtitle cues)")


if __name__ == "__main__":
    main()
