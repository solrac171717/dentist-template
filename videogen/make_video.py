"""Audio-first demo video generator.

Pipeline: script.json -> Kokoro TTS per block -> Whisper word timings ->
timeline (audio is the master clock) -> Playwright records the screen, firing each
action at its anchor word -> ffmpeg trims the video start offset and muxes the audio.
"""
import difflib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
OUT = ROOT / "out"
SCRIPT = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "script.json")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SR = 24000
TAIL = 1.5  # seconds of video after the last word


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


# ---------- 1. audio ----------
def synth_blocks(cfg):
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(ROOT / "models/kokoro-v1.0.onnx"), str(ROOT / "models/voices-v1.0.bin"))
    OUT.mkdir(exist_ok=True)
    paths = []
    for i, b in enumerate(cfg["blocks"]):
        p = OUT / f"block_{i:02d}.wav"
        samples, sr = kokoro.create(b["say"], voice=cfg["voice"], speed=cfg["speed"], lang="en-us")
        assert sr == SR
        sf.write(p, samples, sr)
        paths.append(p)
        print(f"  tts block {i}: {len(samples) / sr:.2f}s")
    return paths


def word_timings(paths):
    from faster_whisper import WhisperModel

    model = WhisperModel("base.en", compute_type="int8")
    result = []
    for p in paths:
        segs, _ = model.transcribe(str(p), word_timestamps=True, language="en")
        result.append([(norm(w.word), w.start, w.end) for s in segs for w in s.words])
    return result


def build_timeline(cfg, paths, words):
    """Returns (full_audio, [(abs_time, action), ...], total_duration)."""
    chunks, events, t = [], [], 0.0
    gap = np.zeros(int(cfg["gap"] * SR), dtype=np.float32)
    for b, p, ws in zip(cfg["blocks"], paths, words):
        audio, _ = sf.read(p, dtype="float32")
        cursor = 0
        for a in b["actions"]:
            target = norm(a["at_word"])
            names = [w[0] for w in ws]
            idx = next((k for k in range(cursor, len(ws)) if names[k] == target), None)
            if idx is None:  # whisper may misspell brand names; fall back to closest match
                best = difflib.get_close_matches(target, names[cursor:], n=1, cutoff=0.6)
                if not best:
                    raise SystemExit(f"anchor word '{a['at_word']}' not found in: {names}")
                idx = cursor + names[cursor:].index(best[0])
            cursor = idx
            events.append((t + ws[idx][1], a))
        chunks += [audio, gap]
        t += len(audio) / SR + cfg["gap"]
    return np.concatenate(chunks), sorted(events, key=lambda e: e[0]), t


# ---------- 2. screen recording ----------
CURSOR_JS = """
(() => {
  const mk = () => {
    if (document.getElementById('__cur')) return;
    const c = document.createElement('div'); c.id = '__cur';
    c.style.cssText = 'position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;transform:translate(-100px,-100px);';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M3 2l7 19 2.6-7.4L20 11z" fill="#fff" stroke="#000" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    addEventListener('mousemove', e => { c.style.transform = `translate(${e.clientX}px,${e.clientY}px)`; }, true);
    addEventListener('mousedown', e => {
      const r = document.createElement('div');
      r.style.cssText = `position:fixed;left:${e.clientX-20}px;top:${e.clientY-20}px;width:40px;height:40px;border-radius:50%;border:3px solid #ffd60a;z-index:2147483647;pointer-events:none;transition:all .5s ease-out;opacity:1`;
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(2)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
  };
  document.readyState === 'loading' ? addEventListener('DOMContentLoaded', mk) : mk();
})();
"""

SMOOTH_SCROLL_JS = """
([y, dur]) => new Promise(res => {
  const y0 = scrollY, t0 = performance.now();
  const ease = t => t < .5 ? 4*t*t*t : 1 - Math.pow(-2*t + 2, 3) / 2;
  const step = now => {
    const t = Math.min(1, (now - t0) / (dur * 1000));
    scrollTo(0, y0 + (y - y0) * ease(t));
    t < 1 ? requestAnimationFrame(step) : res();
  };
  requestAnimationFrame(step);
})
"""


class Runner:
    def __init__(self, page):
        self.page, self.mx, self.my = page, 960, 540

    def _center(self, sel):
        box = self.page.locator(sel).first.bounding_box()
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def move(self, sel, dur=1.0):
        tx, ty = self._center(sel)
        sx, sy = self.mx, self.my
        start = time.monotonic()
        while (el := time.monotonic() - start) < dur:  # clock-driven, so slow mouse calls don't stretch the move
            k = 1 - (1 - el / dur) ** 3
            self.page.mouse.move(sx + (tx - sx) * k, sy + (ty - sy) * k)
            time.sleep(1 / 60)  # ~60 updates/s; unthrottled events back up the page's queue
        self.page.mouse.move(tx, ty)
        self.mx, self.my = tx, ty

    def click(self, sel, dur=1.0):
        self.move(sel, dur)
        self.page.mouse.click(self.mx, self.my)

    def type(self, sel, text, dur=1.5):
        start = time.monotonic()
        for i, ch in enumerate(text):
            wait = start + i * dur / len(text) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.page.keyboard.type(ch)

    def scroll(self, sel, dur=1.5):
        top = self.page.locator(sel).first.evaluate("e => e.getBoundingClientRect().top + scrollY")
        self.page.evaluate(SMOOTH_SCROLL_JS, [max(0, top - 220), dur])

    def run(self, action):
        kw = {k: v for k, v in action.items() if k not in ("at_word", "do", "target")}
        getattr(self, action["do"])(*( [action["target"]] if "target" in action else []), **kw)


def record(cfg, events, duration):
    video_dir = OUT / "video_raw"
    for f in video_dir.glob("*.webm"):
        f.unlink()
    log = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(video_dir),
            record_video_size={"width": 1920, "height": 1080},
        )
        ctx.add_init_script(CURSOR_JS)
        t_created = time.monotonic()  # the recording starts about here
        page = ctx.new_page()
        page.goto(cfg["url"], wait_until="networkidle")
        page.add_style_tag(content="html{scroll-behavior:auto!important}")
        page.mouse.move(960, 540)
        time.sleep(0.5)
        t0 = time.monotonic()  # timeline zero: audio second 0 == this instant
        offset = t0 - t_created
        runner = Runner(page)
        for t_target, a in events:
            wait = t_target - (time.monotonic() - t0)
            if wait > 0:
                time.sleep(wait)
            started = time.monotonic() - t0
            runner.run(a)
            log.append({"target": round(t_target, 2), "started": round(started, 2), "drift": round(started - t_target, 2), **{k: a[k] for k in ("do", "at_word")}})
            print(f"  t={t_target:5.2f}s  drift {started - t_target:+.2f}s  {a['do']} @ '{a['at_word']}'")
        rest = duration + TAIL - (time.monotonic() - t0)
        if rest > 0:
            time.sleep(rest)
        video_path = page.video.path()
        ctx.close()
        browser.close()
    (OUT / "sync_log.json").write_text(json.dumps(log, indent=2))
    return Path(video_path), offset


# ---------- 3. mux ----------
def mux(video, offset, audio, name):
    wav = OUT / "narration.wav"
    sf.write(wav, audio, SR)
    final = OUT / name
    subprocess.run(
        [FFMPEG, "-y", "-ss", f"{offset:.3f}", "-i", str(video), "-i", str(wav),
         "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", "30",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(final)],
        check=True, capture_output=True,
    )
    return final


if __name__ == "__main__":
    cfg = json.loads(SCRIPT.read_text(encoding="utf-8"))
    print("1/4 synthesizing voice")
    paths = synth_blocks(cfg)
    print("2/4 word timings")
    words = word_timings(paths)
    audio, events, total = build_timeline(cfg, paths, words)
    print(f"3/4 recording screen ({total:.1f}s of narration)")
    video, offset = record(cfg, events, total)
    print(f"4/4 muxing (video start offset {offset:.2f}s)")
    print("DONE ->", mux(video, offset, audio, "demo.mp4"))
