# Build an SRT for the demo from the narration that was actually generated.
#
#   python subtitles.py                 # narration/*.wav + DEMO-SCRIPT.md -> demo.srt
#   python subtitles.py --out other.srt
#
# Why not YouTube's auto-captions: this narration is mostly statistical vocabulary
# and spelled-out decimals - "Bonferroni", "residual", "plus zero-four-three-two".
# Automatic captioning of synthetic speech gets those wrong, and a caption that
# misreports a number in a video about misreported numbers is not a small problem.
# We wrote the script, so the text is exact. Only the timing has to be recovered.
#
# Why not synthesise sentence by sentence and time each one exactly: it would work,
# but it changes how the voice track is produced, and joining separately generated
# sentences makes prosody jump at the seams. The audio already works. Subtitles are
# an addition and must not put the thing that works at risk.
#
# So the timing is recovered from the audio instead, using two signals together.
# Character count says where each sentence boundary should fall; detected silence
# says where the voice actually paused; each boundary snaps to the nearest pause
# within tolerance and keeps the estimate when there is none.
#
# Neither signal alone works. Ranking pauses by duration and taking the longest N-1
# fails because the narration direction tells the voice to slow down for numbers,
# so the longest silences in a beat sit mid-sentence around a figure being read
# out - that version put "This is Second Pass." on screen for 5.9 seconds and the
# sentence after it for 1.1. Character count alone ignores that some sentences are
# read far slower than their length implies, which is the whole point of the
# direction. The run prints how many boundaries snapped and the largest correction,
# because a caption file that is silently approximate is worse than one that says so.

import argparse
import array
import io
import os
import re
import sys
import wave

import narrate

WINDOW_MS = 20
MIN_PAUSE_MS = 120
# Silence is relative to the beat's own loudness: the voice is normalised per
# request, so an absolute threshold drifts between files.
SILENCE_FRACTION = 0.10

MAX_LINE = 42      # characters per subtitle line
MAX_CUE_LINES = 2


def read_wav(path):
    with wave.open(path, "rb") as f:
        assert f.getsampwidth() == 2 and f.getnchannels() == 1, path
        rate = f.getframerate()
        pcm = array.array("h")
        pcm.frombytes(f.readframes(f.getnframes()))
    return pcm, rate


def rms_windows(pcm, rate):
    """Mean absolute amplitude per fixed window. Mean-abs, not true RMS: it is
    monotonic in the same way, and squaring 4M samples in pure Python is slow."""
    size = max(1, rate * WINDOW_MS // 1000)
    out = []
    for i in range(0, len(pcm) - size + 1, size):
        chunk = pcm[i:i + size]
        out.append(sum(abs(v) for v in chunk) / size)
    return out, size / rate


def silences(pcm, rate):
    """(start_s, end_s) of every internal pause, longest first."""
    levels, win = rms_windows(pcm, rate)
    if not levels:
        return []
    peak = max(levels)
    if peak <= 0:
        return []
    floor = peak * SILENCE_FRACTION
    runs, start = [], None
    for i, lv in enumerate(levels):
        if lv < floor:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(levels)))

    out = []
    for a, b in runs:
        # A leading or trailing pause is not a sentence boundary.
        if a == 0 or b >= len(levels):
            continue
        if (b - a) * win * 1000 >= MIN_PAUSE_MS:
            out.append((a * win, b * win))
    out.sort(key=lambda p: p[1] - p[0], reverse=True)
    return out


def sentences(text):
    """Split on sentence enders, but never inside a number.

    The script says "plus zero-point-three-four-three" in words, but it also
    carries figures like 1.28 and 0.322 in the stage text; splitting on any period
    would cut those in half.
    """
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z“‘])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def boundaries(pcm, rate, parts):
    """Seconds at which each sentence after the first begins, and how we got them.

    Taking the longest N-1 pauses does not work, and the failure is instructive:
    the narration direction tells the voice to slow down for numbers, so the
    longest silences in a beat are often mid-sentence, around a figure being read
    out. Ranking by duration put "This is Second Pass." on screen for 5.9 seconds
    and the sentence after it for 1.1.

    So the character count sets an expectation and the audio corrects it: for each
    boundary, take the pause nearest where the text says it should fall, never
    earlier than the previous one. A boundary with no pause near it keeps the
    estimate rather than snapping to something implausible far away.
    """
    dur = len(pcm) / rate
    if len(parts) <= 1:
        return [], "single sentence", 0.0

    expected = by_characters(parts, dur)
    gaps = silences(pcm, rate)
    if not gaps:
        return expected, "no pauses found - character estimate", 0.0

    mids = sorted((a + b) / 2 for a, b in gaps)
    tolerance = max(1.5, dur * 0.12)

    cuts, drift, snapped, last = [], 0.0, 0, 0.0
    for want in expected:
        near = [m for m in mids if last < m < dur and abs(m - want) <= tolerance]
        if near:
            got = min(near, key=lambda m: abs(m - want))
            drift = max(drift, abs(got - want))
            snapped += 1
        else:
            got = max(want, last + 0.05)
        cuts.append(got)
        last = got
    how = "%d/%d snapped to a pause" % (snapped, len(expected))
    return cuts, how, drift


def by_characters(text_parts, dur):
    """Fallback: apportion the beat by character count."""
    total = sum(len(p) for p in text_parts) or 1
    cuts, acc = [], 0
    for p in text_parts[:-1]:
        acc += len(p)
        cuts.append(dur * acc / total)
    return cuts


def wrap(text):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_LINE:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def split_long(text, start, end):
    """A sentence too long to read in two lines becomes two cues, split at the
    word nearest the middle so each half keeps its share of the time."""
    lines = wrap(text)
    if len(lines) <= MAX_CUE_LINES:
        return [(start, end, lines)]
    words = text.split()
    half = len(words) // 2
    a, b = " ".join(words[:half]), " ".join(words[half:])
    mid = start + (end - start) * len(a) / max(1, len(a) + len(b))
    return split_long(a, start, mid) + split_long(b, mid, end)


def ts(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="second-pass-demo.srt")
    ap.add_argument("--script", default=narrate.SCRIPT)
    args = ap.parse_args()

    cues, offset, approximated = [], 0.0, []
    for heading, text in narrate.beats(args.script):
        wav = os.path.join(narrate.OUT, narrate.slug(heading) + ".wav")
        if not os.path.exists(wav):
            sys.exit("missing %s - run narrate.py first" % wav)

        pcm, rate = read_wav(wav)
        dur = len(pcm) / rate
        parts = sentences(text)
        cuts, how, drift = boundaries(pcm, rate, parts)
        if "estimate" in how or "snapped" in how and how.startswith("0/"):
            approximated.append(narrate.slug(heading))
        print("%-12s %5.1fs  %d sentences  %s  max drift %.2fs"
              % (narrate.slug(heading), dur, len(parts), how, drift))

        edges = [0.0] + list(cuts) + [dur]
        for i, part in enumerate(parts):
            for a, b, lines in split_long(part, offset + edges[i], offset + edges[i + 1]):
                cues.append((a, b, lines))
        offset += dur

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for i, (a, b, lines) in enumerate(cues, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (i, ts(a), ts(b), "\n".join(lines)))

    print()
    print("%s: %d cues, %d:%02d total" % (args.out, len(cues), offset // 60, offset % 60))
    if approximated:
        print("TIMING APPROXIMATE in: %s (character-count fallback)"
              % ", ".join(approximated), file=sys.stderr)


if __name__ == "__main__":
    main()
