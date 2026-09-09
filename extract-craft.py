# Turn the archived demo-video corpus into one row per video.
#
#   python extract-craft.py --corpus ../hackathon-video-research
#
# The corpus is 73 real hackathon demo videos pulled from Devpost on 2026-09-05,
# 56 of them with English auto-captions. The raw captions live in the archive, not
# in this repo: they are someone else's speech, they are large, and none of the
# analysis needs them again once these numbers exist. What lands here is craft.tsv,
# one row per video, which load.py inserts.
#
# WHAT THIS CORPUS IS FOR, AND WHAT IT IS NOT
#
# These are descriptive medians of what people actually do. They are NOT a rule.
# The corpus was sampled in two batches - winners and non-winners of the same
# events - and the difference between those batches is weak and confounded: the
# median duration is 174s against 175s, and the winners' faster speech tracks their
# being more often teams (44% vs 23% multi-voice) rather than anything about pace.
# So the batches are pooled, the batch label is kept only as provenance, and any
# tool built on this must report a percentile and the spread, never a pass or fail.
# The one exception is a competition's own written rule - a duration cap is a rule,
# a median is not.

import argparse
import io
import os
import re

NL = chr(10)
TAB = chr(9)
LITERAL_BACKSLASH_T = chr(92) + "t"  # what yt-dlp's --print actually emitted

DEMO = (r"\blet me show\b|\bas you can see\b|\bhere (we|you|i)\b|\bwatch\b|\bif i click\b"
        r"|\bnow i\b|\blet'?s (see|look|try|start)\b|\bclick(ing)?\b|\bhere'?s\b"
        r"|\bdemo\b|\bwalk (you )?through\b")
INTRO = r"\b(hi|hello|hey)\b|\bmy name\b|\bi'?m\b|\bwe are\b|\bwelcome\b"
NAMED = r"\bthis is\b|\bintroducing\b|\bmeet\b|\bpresent(ing)?\b"
PROBLEM = (r"\bproblem\b|\bwaste[sd]?\b|\bstruggle\b|\bbroken\b|\bmillions?\b|\bhours?\b"
           r"|\bevery (year|day)\b|\bfail(s|ed|ure)?\b|\bcan'?t\b|\bdoesn'?t\b|\bhard\b"
           r"|\bexpensive\b|\bslow\b|\bmanual(ly)?\b|\bno one\b|\bnobody\b")
TECH = (r"\bgemini\b|\bvertex\b|\bapi\b|\bmodel\b|\bagent\b|\bdatabase\b|\bcloud\b"
        r"|\bpython\b|\breact\b|\bsdk\b|\bmcp\b|\bllm\b|\bstack\b|\bbuilt (it )?(with|on)\b")
CTA = r"\btry it\b|\bvisit\b|\bcheck (it )?out\b|\bgithub\b|\bsign up\b|\blink (in|below)\b"
THANKS = r"\bthank"
NUM = r"\b\d+(\.\d+)?\s*(%|percent|million|billion|thousand|hours?|minutes?|seconds?|x)\b"
SPEAKER = ("&gt;&gt;", ">>")

COLUMNS = [
    "video_id", "batch", "duration_s", "has_captions",
    "words_total", "wpm", "sentence_words",
    "words_first_5s", "words_first_10s", "words_first_15s", "words_first_20s",
    "intro_first_15s", "names_project_first_15s", "problem_first_15s",
    "demo_verb_at_s", "demo_verb_frac", "tech_first_frac",
    "mentions_number", "multi_voice",
    "thanks_at_end", "cta_at_end", "tail_wpm", "silent_tail",
]


def transcript(path):
    """(seconds, new words) per caption cue, with the rolling overlap removed.

    YouTube's auto-captions roll: each cue repeats the tail of the one before it,
    so counting words off the raw file inflates the total roughly threefold. Each
    cue keeps only what it adds to the one before.
    """
    words, timed = [], []
    for block in io.open(path, encoding="utf-8", errors="replace").read().split(NL + NL):
        m = re.search(r"(\d+):(\d\d):(\d\d)\.(\d+) -->", block)
        if not m:
            continue
        t = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
             + int(m.group(4)[:3]) / 1000)
        body = " ".join(l for l in block.split(NL)[1:] if "-->" not in l)
        txt = re.sub(r"<[^>]+>", "", body).strip()
        if not txt:
            continue
        new = txt.split()
        k = 0
        for k in range(min(len(new), len(words)), -1, -1):
            if words[len(words) - k:] == new[:k]:
                break
        add = new[k:]
        if add:
            timed.append((t, " ".join(add)))
            words += add
    return words, timed


def durations(folder):
    out = {}
    path = os.path.join(folder, "meta.tsv")
    for line in io.open(path, encoding="utf-8", errors="replace"):
        parts = line.rstrip(NL).split(TAB)
        if len(parts) < 3:
            continue
        head = parts[2].split(LITERAL_BACKSLASH_T)[0].strip()
        if head.isdigit():
            out[parts[0]] = int(head)
    return out


def said(timed, lo, hi):
    return " ".join(t for s, t in timed if lo <= s < hi)


def first_hit(timed, rx):
    for s, t in timed:
        if re.search(rx, t, re.I):
            return s
    return None


def measure(video_id, batch, dur, timed, words):
    r = {c: "" for c in COLUMNS}
    r["video_id"] = video_id
    r["batch"] = batch
    r["duration_s"] = dur
    r["has_captions"] = 1 if timed else 0
    if not timed:
        return r

    whole = " ".join(t for _, t in timed)
    r["words_total"] = len(words)
    r["wpm"] = round(len(words) / dur * 60, 1) if dur else 0
    sents = [s for s in re.split(r"[.!?]+", whole) if s.strip()]
    r["sentence_words"] = (round(sum(len(s.split()) for s in sents) / len(sents), 1)
                           if sents else 0)

    for w in (5, 10, 15, 20):
        r["words_first_%ds" % w] = len(said(timed, 0, w).split())

    head = said(timed, 0, 15)
    r["intro_first_15s"] = 1 if re.search(INTRO, head, re.I) else 0
    r["names_project_first_15s"] = 1 if re.search(NAMED, head, re.I) else 0
    r["problem_first_15s"] = 1 if re.search(PROBLEM, head, re.I) else 0

    d = first_hit(timed, DEMO)
    r["demo_verb_at_s"] = round(d, 1) if d is not None else ""
    r["demo_verb_frac"] = round(d / dur, 3) if d is not None and dur else ""
    t = first_hit(timed, TECH)
    r["tech_first_frac"] = round(t / dur, 3) if t is not None and dur else ""

    r["mentions_number"] = 1 if re.search(NUM, whole, re.I) else 0
    r["multi_voice"] = 1 if any(s in whole for s in SPEAKER) else 0

    end = timed[-1][0]
    tail15 = said(timed, end - 15, end + 1)
    r["thanks_at_end"] = 1 if re.search(THANKS, tail15, re.I) else 0
    r["cta_at_end"] = 1 if re.search(CTA, tail15, re.I) else 0
    tail10 = said(timed, end - 10, end + 1).split()
    r["tail_wpm"] = round(len(tail10) * 6, 1)
    r["silent_tail"] = 1 if dur - end > 5 else 0
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="../hackathon-video-research")
    ap.add_argument("--out", default="craft.tsv")
    args = ap.parse_args()

    batches = [
        ("winners", os.path.join(args.corpus, "captions", "batch-a-winners")),
        ("nonwinners", os.path.join(args.corpus, "captions", "batch-b-nonwinners")),
    ]
    rows = []
    for batch, folder in batches:
        if not os.path.isdir(folder):
            raise SystemExit("no such corpus folder: %s" % folder)
        for name, dur in sorted(durations(folder).items()):
            vtt = os.path.join(folder, name + ".en.vtt")
            words, timed = transcript(vtt) if os.path.exists(vtt) else ([], [])
            rows.append(measure(name, batch, dur, timed, words))
        print("%-12s %d videos" % (batch, sum(1 for r in rows if r["batch"] == batch)))

    with io.open(args.out, "w", encoding="utf-8", newline=NL) as f:
        f.write(TAB.join(COLUMNS) + NL)
        for r in rows:
            f.write(TAB.join(str(r[c]) for c in COLUMNS) + NL)

    subs = sum(1 for r in rows if r["has_captions"])
    print()
    print("%s: %d videos, %d with captions" % (args.out, len(rows), subs))


if __name__ == "__main__":
    main()
