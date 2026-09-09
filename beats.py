# Merge detected shots into beats, and cut a clip for each.
#
#   python beats.py tos            # films/tos/beats.json + films/tos/beat_clips/
#   python beats.py tos --min 20
#
# WHY THIS EXISTS
#
# The 100-bin grid comes from YouTube's heatmap and has nothing to do with the film:
# a bin is 7.34s on Tears of Steel while the median shot is 3.2s, so 69 of 100 bins
# straddle at least one cut and the average bin holds 2.4 shot fragments. Every
# content score is therefore an average over heterogeneous material, and averaging
# heterogeneous material attenuates every correlation computed from it. That is a
# third possible reading of this project's null, alongside "content really does not
# predict attention" and "the scores are too noisy" - and unlike those two, it is
# cheap to test.
#
# WHAT THIS IS NOT FOR
#
# Not a second statistical test at beat level. There would be about 30 beats per
# film, and 2.6383/sqrt(29) is a corrected floor of 0.490 - nothing at these effect
# sizes can clear that. Detrending at beat level would cost another 8 beats at each
# edge and push the floor past 0.57. That is arithmetic, and no amount of cleaner
# scoring fixes it.
#
# So beats are scored, and then each BIN inherits the score of the beat it sits in.
# The correlation still runs at n = 64/66/68 against the same floors as before, with
# x measured on coherent material instead of on blends. If the correlations rise,
# the segmentation was attenuating them. If they do not, the null is firmer than it
# was. Either way the sample size is untouched, which is the whole point.
#
# Beats also carry the text layer - transcript and a blind prose description - and
# that use needs no statistical power at all.

import argparse
import io
import json
import os
import subprocess
import sys

MIN_BEAT_S = 15.0


def film_region(film_id, films, meta):
    """Bins that are film, by the same midpoint rule prepare.py uses."""
    dur = float(meta["duration"])
    bw = dur / 100.0
    cs = films[film_id].get("credits_start_s")
    n = (100 if cs is None
         else sum(1 for i in range(100) if (i + 0.5) * bw < cs))
    return dur, bw, n


def merge(cuts, start, end, min_s):
    """Consecutive shots joined until each run reaches min_s.

    A beat boundary is always a real detected cut, never an arbitrary time: the
    point is to stop splitting shots, so inventing a boundary inside one would
    defeat the exercise. The last beat absorbs whatever is left rather than being
    allowed to fall under the minimum, because a 3-second final beat scored as a
    scene is exactly the blend this is meant to remove.
    """
    inside = [c for c in cuts if start < c < end]
    beats, last = [], start
    for c in inside:
        if c - last >= min_s:
            beats.append((last, c))
            last = c
    if beats and end - last < min_s:
        beats[-1] = (beats[-1][0], end)
    else:
        beats.append((last, end))
    return beats


def main(film_id, min_s):
    films = json.load(io.open("films.json", encoding="utf-8"))
    film = films[film_id]
    meta = json.load(io.open(film["meta"], encoding="utf-8"))
    dur, bw, n_film = film_region(film_id, films, meta)

    shots_path = os.path.join("films", film_id, "shots.txt")
    if not os.path.exists(shots_path):
        sys.exit("no %s - run the shot detection first" % shots_path)
    cuts = sorted(float(x) for x in io.open(shots_path) if x.strip())

    film_end = n_film * bw
    beats = merge(cuts, 0.0, film_end, min_s)

    out_dir = os.path.join("films", film_id)
    clip_dir = os.path.join(out_dir, "beat_clips")
    os.makedirs(clip_dir, exist_ok=True)

    rows = []
    for i, (a, b) in enumerate(beats):
        # Which bins this beat covers, and how much of each. The scores travel the
        # other way later - bin inherits beat - but the overlap is recorded here so
        # that mapping never has to be re-derived from timecodes.
        lo, hi = int(a // bw), int((b - 1e-6) // bw)
        rows.append({
            "film": film_id,
            "beat": i,
            "start": round(a, 3),
            "end": round(b, 3),
            "duration": round(b - a, 3),
            "shots": sum(1 for c in cuts if a < c < b) + 1,
            "bins": list(range(lo, min(hi, n_film - 1) + 1)),
        })

    json.dump(rows, io.open(os.path.join(out_dir, "beats.json"), "w", encoding="utf-8"),
              indent=1)

    lens = [r["duration"] for r in rows]
    print("%s: %d beats over %d film bins (%.0fs)" % (film_id, len(rows), n_film, film_end))
    print("  beat length: median %.1fs  min %.1fs  max %.1fs"
          % (sorted(lens)[len(lens) // 2], min(lens), max(lens)))
    print("  shots per beat: median %d  max %d"
          % (sorted(r["shots"] for r in rows)[len(rows) // 2],
             max(r["shots"] for r in rows)))

    made = skipped = 0
    for r in rows:
        target = os.path.join(clip_dir, "beat_%03d.mp4" % r["beat"])
        if os.path.exists(target):
            skipped += 1
            continue
        # Integer seconds because ffmpeg under a zh-CN locale rejects a decimal
        # -ss/-t, and the error names a character you cannot see.
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error",
             "-ss", str(int(r["start"])),
             "-i", film["video"],
             "-t", str(max(2, int(round(r["duration"])))),
             "-vf", "scale=-2:360,fps=2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
             "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-ar", "16000",
             "-y", target],
            check=True,
        )
        made += 1
    print("  clips: %d new, %d already there" % (made, skipped))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("film")
    ap.add_argument("--min", type=float, default=MIN_BEAT_S)
    args = ap.parse_args()
    main(args.film, args.min)
