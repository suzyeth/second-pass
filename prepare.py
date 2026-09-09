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

    # The heatmap covers the whole upload, and the upload ends with credits. Those
    # bins are not film: on Tears of Steel the film averages 0.074 and the credits
    # 0.591, and the upload's maximum attention sits at bin 97, deep in the roll.
    # Because YouTube min-max normalises per upload, leaving them in also squeezes
    # the entire film into the bottom tenth of the scale.
    #
    # Truncating before detrending is the part that matters. The baseline is a
    # +-8-bin moving average, so if the credits stay in, the window for the last
    # stretch of FILM reaches into them and that baseline is pulled toward a
    # region eight times higher. Filtering the credits out of the correlation
    # afterwards does not undo it - the residuals are already wrong.
    values_all = [h["value"] for h in heatmap]
    bin_w = float(meta["duration"]) / len(heatmap)
    credits_start = film.get("credits_start_s")
    # A bin belongs to the credits when its MIDPOINT does. Keying on its start
    # keeps a bin that is 94% credits, and on Big Buck Bunny that single segment
    # was enough to move a feature across the significance floor and back.
    n_film = (len(heatmap) if credits_start is None else
              sum(1 for i in range(len(heatmap)) if (i + 0.5) * bin_w < credits_start))

    values = values_all[:n_film]
    base, residual = detrend(values)

    out_dir = os.path.join("films", film_id)
    clip_dir = os.path.join(out_dir, "clips")
    os.makedirs(clip_dir, exist_ok=True)

    bins = []
    for i, h in enumerate(heatmap):
        is_credits = i >= n_film
        # Edges are relative to the FILM region, not the upload. A bin 8 from the
        # end of the film used to look interior because 20 credit bins sat behind
        # it; its baseline was one-sided all along, just filled with the wrong data.
        edge = is_credits or i < BASELINE_WINDOW or i >= n_film - BASELINE_WINDOW
        bins.append(
            {
                "film": film_id,
                "bin": i,
                "start": h["start_time"],
                "end": h["end_time"],
                "raw": values_all[i],
                # Credits have no valid baseline - there is no film around them to
                # average - so they carry base = raw and res = 0 rather than a
                # number that could be mistaken for a measurement.
                "base": base[i] if not is_credits else values_all[i],
                "res": residual[i] if not is_credits else 0.0,
                "credits": is_credits,
                # Credits are marked as edge as well. The two flags mean different
                # things, but every existing query filters on is_edge = 0, so this
                # makes the exclusion fail-safe instead of depending on each of
                # them being found and updated.
                "edge": edge,
            }
        )

    json.dump(bins, open(os.path.join(out_dir, "bins.json"), "w", encoding="utf-8"), indent=1)
    print(f"{film_id}: {len(bins)} bins, {round(meta['duration']/len(bins),2)}s each")
    print(f"  film region: bins 0-{n_film - 1} ({n_film}), credits: {len(bins) - n_film}")
    print(f"  usable after edges: {sum(1 for b in bins if not b['edge'])}")
    print(f"  residual sd = {st.pstdev(residual):.4f}  (film region only)")

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
