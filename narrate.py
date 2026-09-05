# Turn DEMO-SCRIPT.md's narration into audio, one file per beat.
#
#   python narrate.py            # all beats -> narration/
#   python narrate.py --voice Kore
#
# Google's TTS, because Agentic Cinema permits Google AI only: "No other AI
# models, agent frameworks, or AI APIs are permitted." That rules out the usual
# voice services regardless of how they sound.
#
# One file per beat rather than one long file. A beat that comes out wrong is
# then re-generated on its own, and the per-beat durations are what tell you
# whether the script actually fits under three minutes — the 150-words-per-minute
# estimate in the script header is an estimate, and the cap is a hard rule.

import argparse
import io
import os
import re
import struct
import sys
import time
import wave

from google import genai
from google.genai import types

MODEL = os.environ.get("TTS_MODEL", "gemini-2.5-flash-preview-tts")
OUT = "narration"
SCRIPT = "DEMO-SCRIPT.md"

# Read as one continuous take rather than as isolated sentences: the beats are
# consecutive in the finished video, and TTS given a bare fragment tends to end
# every line on a falling full stop.
DIRECTION = (
    "Read this as one section of a continuous technical narration for a product "
    "demo. Measured and matter-of-fact, not salesy, not dramatic. Numbers are the "
    "point, so give them room: read them clearly and do not rush past them."
)


def beats(path):
    """(heading, spoken text) per section, in order.

    The spoken lines are the blockquotes; everything else in the script is stage
    direction for whoever is driving the screen, and must not be read aloud.
    """
    out = []
    heading, said = None, []
    for line in io.open(path, encoding="utf-8"):
        if line.startswith("## "):
            if heading and said:
                out.append((heading, " ".join(said)))
            heading, said = line[3:].strip(), []
        elif line.strip().startswith(">"):
            text = line.strip().lstrip("> ").strip()
            if text:
                said.append(text)
    if heading and said:
        out.append((heading, " ".join(said)))
    return out


def slug(heading):
    """0:22–0:55 — 位置假象  ->  0022-0055"""
    stamps = re.findall(r"(\d+):(\d\d)", heading)
    if stamps:
        return "-".join(m + s for m, s in stamps)
    return re.sub(r"[^a-z0-9]+", "-", heading.lower())[:24].strip("-")


def to_wav(path, pcm, rate=24000):
    """The API returns raw signed 16-bit little-endian mono PCM, not a container."""
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)


def rate_of(mime):
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else 24000


def speak(client, text, voice, tries=4):
    for attempt in range(tries):
        try:
            r = client.models.generate_content(
                model=MODEL,
                contents=DIRECTION + "\n\n" + text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice
                            )
                        )
                    ),
                ),
            )
            blob = r.candidates[0].content.parts[0].inline_data
            return blob.data, rate_of(blob.mime_type)
        except Exception as exc:  # noqa: BLE001 - retried below
            text_exc = str(exc)
            if attempt == tries - 1 or not any(
                m in text_exc for m in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")
            ):
                raise
            wait = (15, 45, 90)[attempt]
            print(f"    quota/overload, waiting {wait}s", flush=True)
            time.sleep(wait)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="Charon")
    ap.add_argument("--only", help="substring of a beat heading")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    total_words = total_secs = 0
    for heading, text in beats(SCRIPT):
        if args.only and args.only not in heading:
            continue
        name = os.path.join(OUT, slug(heading) + ".wav")
        print(f"{slug(heading):<12} {len(text.split()):>4} words  {heading[:44]}")

        pcm, rate = speak(client, text, args.voice)
        to_wav(name, pcm, rate)

        secs = len(pcm) / (rate * 2)
        total_words += len(text.split())
        total_secs += secs
        print(f"             -> {name}  {secs:5.1f}s")

    print()
    print(f"total: {total_words} words, {total_secs:.0f}s "
          f"({int(total_secs // 60)}:{int(total_secs % 60):02d})")
    if total_secs > 180:
        print("OVER THE 3:00 CAP — cut narration before recording", file=sys.stderr)
        sys.exit(1)
    print(f"under the 3:00 cap with {180 - total_secs:.0f}s to spare")


if __name__ == "__main__":
    main()
