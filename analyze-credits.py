# What the corpus looks like once the end credits are not treated as film.
#
#   python analyze-credits.py
#
# The 100-bin grid covers the whole YouTube upload, and the upload ends with
# credits. Frame sampling pins the boundary to 588s on Tears of Steel, 489.5s on
# Big Buck Bunny and 744s on Sintel - roughly a fifth of each upload, and not film.
#
# Tears of Steel also has a POST-CREDITS SCENE, ~711-734s, which IS film. It is
# excluded anyway: three bins separated from the body by 123s of credits have no
# valid local baseline. It is also where the upload's maximum attention sits -
# people jump to the stinger - and counting that as "the film gets more watched
# toward the end" is what produced a position_bias of 4.40 where the film's own
# figure is 1.28.
#
# Filtering those rows out of the correlation is NOT enough, and the first
# version of this script made exactly that mistake. `attention_base` is a +-8-bin
# moving average, so for the last stretch of the film the window reaches into the
# credits and the baseline is pulled toward a region whose attention is 8x
# higher. Every residual near the end of the film is then an artifact of data
# that is not film. The curve has to be truncated first and the baseline
# recomputed on what is left, which is what this script does.
#
# Durations and boundaries are read from films.json and the per-film meta, never
# from a constant typed here: an earlier version carried a hand-written 754.0 for
# Tears of Steel when the real duration is 734.0, and a 2.7% error in bin width
# silently moved which segments counted as credits.

import io
import json
import statistics as st
from math import comb

BASELINE_WINDOW = 8  # same as prepare.py; changing one without the other is a bug

FIELDS = [
    "visual_event_density",
    "story_information",
    "character_presence",
    "speech_density",
    "score_intensity",
    "inertness",
]
EXPECTED = {f: (-1 if f == "inertness" else 1) for f in FIELDS}

FILMS = {}
for _fid, _f in json.load(io.open("films.json", encoding="utf-8")).items():
    _meta = json.load(io.open(_f["meta"], encoding="utf-8"))
    FILMS[_fid] = (float(_meta["duration"]), float(_f["credits_start_s"]),
                   [float(x) for x in _f["credits_bracket_s"]])


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out

    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (sum((rx[i] - mx) ** 2 for i in range(n))
           * sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / den if den else 0.0


def scored(film):
    return sorted(
        (r for r in json.load(io.open("films/%s/scored.json" % film, encoding="utf-8"))
         if "inertness" in r),
        key=lambda r: r["bin"],
    )


def analyse(film, credits_start):
    """Truncate at the credits, recompute the baseline, return residuals.

    A bin belongs to the credits when its MIDPOINT does; keying on its start
    keeps a bin that is 94% credits, and on Big Buck Bunny that single segment
    was enough to move a feature across the floor and back.
    """
    dur = FILMS[film][0]
    bw = dur / 100.0
    rows = [r for r in scored(film) if (r["bin"] + 0.5) * bw < credits_start]

    v = [r["raw"] for r in rows]
    n = len(v)
    base = [st.mean(v[max(0, i - BASELINE_WINDOW):min(n, i + BASELINE_WINDOW + 1)])
            for i in range(n)]
    res = [v[i] - base[i] for i in range(n)]

    # Edges are relative to the TRUNCATED curve. The old is_edge flag was cut
    # against the full 100-bin upload, so it protected bins that no longer need
    # protecting and left exposed the ones that now do.
    keep = [(rows[i], res[i]) for i in range(n)
            if BASELINE_WINDOW <= i < n - BASELINE_WINDOW]
    return rows, keep


def correlations(keep):
    out = {}
    for f in FIELDS:
        vals = [r[f] for r, _ in keep]
        out[f] = (spearman(vals, [x for _, x in keep])
                  if len(set(vals)) > 1 else None)
    return out


def verdict(r, n, field):
    if r is None:
        return "undefined"
    crit = 2.6383 / (n - 1) ** 0.5
    one = 1.96 / (n - 1) ** 0.5
    right = r * EXPECTED[field] > 0
    if abs(r) > crit and right:
        return "CLEARS"
    if abs(r) > one and right:
        return "uncorrected only"
    return "-"


def main():
    print("=" * 78)
    print("正片区重算基线后的相关表")
    print("=" * 78)

    res_by_film = {}
    for film, (dur, cs, br) in FILMS.items():
        bw = dur / 100.0
        rows, keep = analyse(film, cs)
        n = len(keep)
        corr = correlations(keep)
        res_by_film[film] = corr
        print()
        print("### %s   时长 %.0fs   字幕 @%.1fs (bin %d)   正片 %d bin，去边缘后 n=%d"
              % (film, dur, cs, int(cs / bw), len(rows), n))
        print("    修正地板 %.3f   未修正 %.3f"
              % (2.6383 / (n - 1) ** 0.5, 1.96 / (n - 1) ** 0.5))

        # Position bias on the film region only. The 4.40x this project was built
        # around is credits: on Tears of Steel the film averages 0.074 and the
        # credits 0.591, and the film's maximum sits at bin 97.
        v = [r["raw"] for r in rows]
        h = len(v) // 2
        print("    position_bias (正片区) %.2fx" % (st.mean(v[h:]) / st.mean(v[:h])))

        for f in FIELDS:
            r = corr[f]
            print("    %-24s %s   %s"
                  % (f, ("  n/a  " if r is None else "%+7.3f" % r), verdict(r, n, f)))

        # Two different questions, and conflating them is how a real result gets
        # thrown away or a fragile one gets kept.
        #
        #   uncertainty  the window frame sampling actually leaves open - the last
        #                frame confirmed as film and the first confirmed as credits.
        #                A verdict that moves inside THIS window is not a verdict:
        #                we do not know which side of it is true.
        #   fragility    a full bin either way. We know the boundary far better than
        #                that, so this is not an alternative analysis - it is a
        #                measure of how few segments the result rests on. Reported,
        #                not used to disqualify.
        def moves(lo, hi):
            out = []
            for f in FIELDS:
                seen = set()
                for cut in (lo, cs, hi):
                    _, k2 = analyse(film, cut)
                    seen.add(verdict(correlations(k2)[f], len(k2), f))
                if len(seen) > 1:
                    out.append("%s(%s)" % (f, "/".join(sorted(seen))))
            return out

        u = moves(br[0], br[1])
        g = moves(cs - bw, cs + bw)
        print("    边界不确定性 %.0f-%.0fs (抽帧实测): %s"
              % (br[0], br[1], "翻转: " + ", ".join(u) if u else "无 verdict 翻转"))
        print("    脆弱性 ±1 bin (假设性): %s"
              % ("依赖少量段: " + ", ".join(g) if g else "稳健"))

    comp = [f for f in FIELDS if all(res_by_film[x][f] is not None for x in FILMS)]
    agree = sum(1 for f in comp
                if all(res_by_film[x][f] > 0 for x in FILMS)
                or all(res_by_film[x][f] < 0 for x in FILMS))
    n = len(comp)
    p = min(1.0, sum(comb(n, k) for k in range(n + 1)
                     if min(k, n - k) <= min(agree, n - agree)) / 2 ** n)
    print()
    print("=" * 78)
    print("跨片方向一致 %d/%d   双尾符号检验 p = %.3f" % (agree, n, p))
    for f in comp:
        print("  %-24s %s" % (f, "  ".join("%s %+.3f" % (x, res_by_film[x][f])
                                           for x in FILMS)))


if __name__ == "__main__":
    main()
