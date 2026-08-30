# Turn one film into every segment the scorer will need.
#
#   python prepare.py tos            # uses films.json
#
# Writes films/<id>/bins.json and films/<id>/clips/bin_NNN.mp4.
# Idempotent: clips that already exist are left alone, so this is safe to re-run
# after an interrupted extraction.
#
# Two Windows-specific traps are handled here rather than left for the caller:
# ffmpeg under a zh-CN locale rejects a decimal -ss/-t, so seeks are integer
# seconds; and anything shelled out to must not carry a '\r', which is invisible
# in ffmpeg's error message.

import json
import os
import statistics as st
import subprocess
import sys

BASELINE_WINDOW = 8  # +-8 bins: "how much attention does this POSITION get?"


def detrend(values):
    """Residual against a local moving average.

    Raw attention is dominated by position — it climbs monotonically toward the
    end of a film — so the raw extremes are a position sample, not a content
    sample. The residual asks the only question worth asking: is this stretch
    watched more or less than its position predicts?
    """
    n = len(values)
    base = [
        st.mean(values[max(0, i - BASELINE_WINDOW) : min(n, i + BASELINE_WINDOW + 1)])
        for i in range(n)
    ]
    return base, [values[i] - base[i] for i in range(n)]


def main(film_id):
    films = json.load(open("films.json", encoding="utf-8"))
    film = films[film_id]

    meta = json.load(open(film["meta"], encoding="utf-8"))
    heatmap = meta.get("heatmap")
    if not heatmap:
        raise SystemExit(f"{film_id}: no heatmap — nothing to measure against")

    values = [h["value"] for h in heatmap]
    base, residual = detrend(values)

    out_dir = os.path.join("films", film_id)
    clip_dir = os.path.join(out_dir, "clips")
    os.makedirs(clip_dir, exist_ok=True)

    bins = []
    for i, h in enumerate(heatmap):
        bins.append(
            {
                "film": film_id,
                "bin": i,
                "start": h["start_time"],
                "end": h["end_time"],
                "raw": values[i],
                "base": base[i],
                "res": residual[i],
                # Edge bins have a one-sided baseline, so their residual is an
                # artifact of the window rather than a measurement. Marked here
                # so the analysis can exclude them without re-deriving the rule.
                "edge": i < BASELINE_WINDOW or i >= len(heatmap) - BASELINE_WINDOW,
            }
        )

    json.dump(bins, open(os.path.join(out_dir, "bins.json"), "w", encoding="utf-8"), indent=1)
    print(f"{film_id}: {len(bins)} bins, {round(meta['duration']/len(bins),2)}s each")
    print(f"  residual sd = {st.pstdev(residual):.4f}")

    made = skipped = 0
    for b in bins:
        target = os.path.join(clip_dir, f"bin_{b['bin']:03d}.mp4")
        if os.path.exists(target):
            skipped += 1
            continue
        # Clip length follows the bin, not a constant. A fixed 7s cut bleeds
        # into the next bin on any film shorter than ~12 min (bbb bins are
        # 5.97s), which smears adjacent segments' content into each other's
        # scores. Integer seconds because zh-CN ffmpeg rejects decimals.
        clip_len = max(2, round(b["end"] - b["start"]))
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error",
                "-ss", str(int(b["start"])),
                "-i", film["video"],
                "-t", str(clip_len),
                "-vf", "scale=-2:360,fps=2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-ar", "16000",
                "-y", target,
            ],
            check=True,
        )
        made += 1

    print(f"  clips: {made} new, {skipped} already there")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tos")
