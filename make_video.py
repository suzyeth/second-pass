# One command from script to finished video.
#
#   python make_video.py                 # rebuild everything that changed
#   python make_video.py --beat 2:35     # redo one beat's narration, then rebuild
#   python make_video.py --skip-record   # re-mux only, when only audio changed
#
# The loop this exists for: change a sentence in DEMO-SCRIPT.md, get a new video.
# Doing that by hand is four commands and two easy mistakes — forgetting to
# regenerate the beat you edited, or re-muxing the old recording against new
# narration so the picture and the voice disagree.
#
# What it does, in order:
#   1. regenerate narration for any beat whose text changed since its .wav
#   2. refuse to continue if the total runs over the competition's 3:00 cap
#   3. drive Chrome through the beats on the clock those .wav files define
#   4. record the screen, crop the taskbar, scale to 1080p, mux the voice
#
# Chrome must be running with --remote-debugging-port=9222 on the deployed page;
# `--launch` starts one with a throwaway profile so the recording shows no
# bookmarks, no profile picture and no other tabs.

import argparse
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import wave

import narrate
import record as recorder

URL = os.environ.get("DEMO_URL",
                     "https://second-pass-334984245629.us-central1.run.app")
OUT = "second-pass-demo.mp4"
WORK = os.path.join(os.environ.get("TEMP", "."), "second-pass-video")
CAP = 180.0


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def stamp_path(wav):
    return wav + ".txt"


def stale_beats(force=None):
    """Beats whose script text no longer matches the audio on disk.

    The hash of the spoken text is stored next to each .wav. Without it there is
    no way to tell an edited beat from an untouched one, and the choice is
    between regenerating all six every time (slow, and burns TTS quota) or
    trusting yourself to remember (which is how a video ships with one beat of
    stale narration).
    """
    out = []
    for heading, text in narrate.beats(narrate.SCRIPT):
        wav = os.path.join(narrate.OUT, narrate.slug(heading) + ".wav")
        want = digest(text)
        have = None
        if os.path.exists(stamp_path(wav)):
            have = io.open(stamp_path(wav), encoding="utf-8").read().strip()
        if force and force in heading:
            out.append((heading, text, wav, "forced"))
        elif not os.path.exists(wav):
            out.append((heading, text, wav, "missing"))
        elif have != want:
            out.append((heading, text, wav, "script changed"))
    return out


def regenerate(stale, voice):
    from google import genai

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    for heading, text, wav, why in stale:
        print(f"  narration: {narrate.slug(heading):<12} ({why})", flush=True)
        pcm, rate = narrate.speak(client, text, voice)
        narrate.to_wav(wav, pcm, rate)
        io.open(stamp_path(wav), "w", encoding="utf-8").write(digest(text))


def total_seconds():
    return sum(secs for _, secs in recorder.beat_lengths())


def build_voice():
    os.makedirs(WORK, exist_ok=True)
    listing = os.path.join(WORK, "list.txt")
    lines = ["file '%s'" % os.path.abspath(p).replace("\\", "/")
             for p in sorted(glob.glob(os.path.join(narrate.OUT, "*.wav")))]
    io.open(listing, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    voice = os.path.join(WORK, "voice.wav")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", listing, "-ar", "48000", "-ac", "2", voice])
    return voice


def launch_chrome():
    profile = os.path.join(WORK, "chrome-profile")
    os.makedirs(profile, exist_ok=True)
    exe = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                       "Google", "Chrome", "Application", "chrome.exe")
    subprocess.Popen([
        exe, "--remote-debugging-port=9222", f"--user-data-dir={profile}",
        "--start-maximized", "--lang=en-US",
        "--disable-features=Translate,TranslateUI",
        "--no-first-run", "--no-default-browser-check",
        "--hide-crash-restore-bubble", URL,
    ])
    time.sleep(16)


def record(seconds):
    raw = os.path.join(WORK, "raw.mp4")
    ff = subprocess.Popen([
        "ffmpeg", "-y", "-v", "error", "-f", "gdigrab", "-framerate", "30",
        "-t", str(int(seconds) + 5), "-i", "desktop",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", raw,
    ])
    time.sleep(2)
    run([sys.executable, "record.py"])
    ff.wait(timeout=120)
    return raw


def compose(raw, voice, seconds):
    run([
        "ffmpeg", "-y", "-v", "error", "-i", raw, "-i", voice,
        "-filter_complex",
        "[0:v]crop=2560:1440:0:0,scale=1920:1080:flags=lanczos,fps=30[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "160k", "-t", str(round(seconds + 1)), OUT,
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beat", help="substring of a beat heading to force-regenerate")
    ap.add_argument("--voice", default="Charon")
    ap.add_argument("--skip-record", action="store_true",
                    help="re-mux the existing recording against new narration")
    ap.add_argument("--launch", action="store_true", help="start Chrome first")
    args = ap.parse_args()

    stale = stale_beats(args.beat)
    if stale:
        regenerate(stale, args.voice)
    else:
        print("  narration: up to date")

    secs = total_seconds()
    print(f"  runtime:   {int(secs // 60)}:{int(secs % 60):02d}", flush=True)
    if secs > CAP:
        # Refusing here rather than after the recording, because the cap is a
        # disqualification and a long take costs four minutes to discover it.
        sys.exit(f"OVER THE {int(CAP)}s CAP by {secs - CAP:.0f}s — cut the script")

    voice = build_voice()

    raw = os.path.join(WORK, "raw.mp4")
    if not args.skip_record:
        if args.launch:
            launch_chrome()
        raw = record(secs)
    elif not os.path.exists(raw):
        sys.exit("--skip-record needs a previous recording, and there is none")

    compose(raw, voice, secs)
    size = os.path.getsize(OUT) / 1048576
    print(f"  {OUT}  {int(secs // 60)}:{int(secs % 60):02d}  {size:.1f} MB")


if __name__ == "__main__":
    main()
