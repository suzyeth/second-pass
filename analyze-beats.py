# Does scoring on scene boundaries instead of the heatmap grid change anything?
#
#   python analyze-beats.py
#
# THE QUESTION
#
# A bin is 7.34s on Tears of Steel and the median shot is 3.2s, so 69 of 100 bins
# straddle at least one cut and the average bin holds 2.4 shot fragments. Every
# content score is an average over heterogeneous material, and averaging
# heterogeneous material attenuates any correlation computed from it. That is a
# third reading of this project's null, next to "content does not predict attention"
# and "the scores are too noisy to tell".
#
# THE DESIGN, AND WHY IT IS NOT A SECOND TEST AT BEAT LEVEL
#
# There are about 30 beats per film. A Bonferroni floor at n=30 is 0.490, and
# detrending at beat level would cost 8 more at each edge and push it past 0.57.
# Nothing at these effect sizes clears that, so a beat-level correlation would be
# uninformative by construction - it could only ever return "no", whatever is true.
#
# So the beats are scored, and then each BIN inherits the score of the beat its
# midpoint falls in. Same n, same floors, same residuals - the only thing that
# changes is that x was measured on coherent material. If the correlations rise,
# segmentation was attenuating them. If they do not, the null is firmer.
#
# The midpoint rule, not a duration-weighted blend of overlapping beats: blending is
# the thing being removed, and reintroducing it at the mapping step would test
# nothing. It is the same rule used for the credits boundary, for the same reason.
#
# THE FLOOR ON THE BEAT SIDE IS NOT THE FLOOR ON THE BIN SIDE
#
# Mapping beat scores onto bins keeps the row count at 64/66/68, but those rows
# carry only 25/21/~24 independent content measurements - each beat score is reused
# by two or three bins. Judging that against a floor computed from 64 would be
# counting one measurement three times and then claiming the precision of three.
# So the beat column is judged against a floor built from the number of DISTINCT
# beats, which is the honest effective n and is much less forgiving: 2.6383/sqrt(24)
# is 0.539 on Tears of Steel against 0.332 for the bins.
#
# That means this experiment cannot produce a significant beat-level result. It was
# never able to - about 30 beats per film was always going to be underpowered. What
# it CAN do is compare the two columns: if blending were badly attenuating a real
# signal, scoring coherent scenes should raise the correlations. Whether it does is
# the whole answer here, and it does not need either column to clear anything.
#
# WHAT THIS CANNOT SETTLE
#
# Beat scores and bin scores come from the same prompt, model, temperature and
# resolution, so the comparison is fair. But neither has a reliability estimate, so
# a difference between them could still be resampling noise rather than the
# segmentation. Two runs of the same clip have never been compared. Until they are,
# read a change here as suggestive.

import io
import json
import statistics as st
import importlib.util as _u

_spec = _u.spec_from_file_location("credits", "analyze-credits.py")
credits = _u.module_from_spec(_spec)
_spec.loader.exec_module(credits)

FIELDS = credits.FIELDS
EXPECTED = credits.EXPECTED
FILMS = credits.FILMS


def beat_scores(film):
    rows = json.load(io.open("films/%s/scored-beats.json" % film, encoding="utf-8"))
    return {r["beat"]: r for r in rows if "inertness" in r}


def beat_spans(film):
    return json.load(io.open("films/%s/beats.json" % film, encoding="utf-8"))


def remap(film, keep):
    """Give every kept bin the scores of the beat its midpoint sits in.

    Returns (rows_with_beat_scores, how_many_bins_had_no_scored_beat).
    """
    dur = FILMS[film][0]
    bw = dur / 100.0
    spans = beat_spans(film)
    scores = beat_scores(film)

    out, missing = [], 0
    for row, res in keep:
        mid = (row["bin"] + 0.5) * bw
        hit = next((b for b in spans if b["start"] <= mid < b["end"]), None)
        if hit is None or hit["beat"] not in scores:
            missing += 1
            continue
        merged = dict(row)
        for f in FIELDS:
            merged[f] = scores[hit["beat"]][f]
        merged["_beat"] = hit["beat"]
        out.append((merged, res))
    return out, missing


def main():
    print("=" * 88)
    print("按 bin 评分 vs 按 beat 评分 —— 同一批残差、同一个 n、同一套地板")
    print("=" * 88)

    agree = {}
    for film, (dur, cs, _br) in FILMS.items():
        _rows, keep = credits.analyse(film, cs)
        n = len(keep)
        crit = 2.6383 / (n - 1) ** 0.5
        one = 1.96 / (n - 1) ** 0.5

        by_bin = credits.correlations(keep)
        remapped, missing = remap(film, keep)
        by_beat = credits.correlations(remapped)

        spans = beat_spans(film)
        distinct = len({r["_beat"] for r, _ in remapped})
        beat_crit = 2.6383 / (distinct - 1) ** 0.5
        print()
        print("### %s   n=%d   修正地板 %.3f   未修正 %.3f" % (film, n, crit, one))
        print("    %d beat 覆盖这 %d 个 bin（语料共 %d beat）%s"
              % (distinct, len(remapped), len(spans),
                 "，%d 个 bin 没有对应的已评分 beat" % missing if missing else ""))
        print("    beat 侧有效 n=%d，地板 %.3f（不是 %.3f——同一个分数被 2-3 个 bin 重用）"
              % (distinct, beat_crit, crit))
        print("    %-24s %9s %9s %9s   %s"
              % ("feature", "bin 评分", "beat 评分", "变化", "verdict"))
        print("    " + "-" * 74)
        for f in FIELDS:
            a, b = by_bin[f], by_beat[f]
            if a is None or b is None:
                print("    %-24s %9s %9s %9s   undefined" % (f, "n/a", "n/a", "-"))
                continue
            va = credits.verdict(a, n, f)
            # Effective n, not row count - see the note at the top.
            vb = credits.verdict(b, distinct, f)
            mark = "  <<<" if va != vb else ""
            print("    %-24s %+9.3f %+9.3f %+9.3f   %s -> %s%s"
                  % (f, a, b, b - a, va, vb, mark))
        agree[film] = by_beat

    # Whether the direction agreement moves is a separate question from whether any
    # single correlation does, and it is the one the project's headline rests on.
    comp = [f for f in FIELDS if all(agree[x][f] is not None for x in FILMS)]
    ok = sum(1 for f in comp
             if all(agree[x][f] > 0 for x in FILMS) or all(agree[x][f] < 0 for x in FILMS))
    from math import comb
    n = len(comp)
    p = min(1.0, sum(comb(n, k) for k in range(n + 1)
                     if min(k, n - k) <= min(ok, n - ok)) / 2 ** n)
    print()
    print("=" * 88)
    print("beat 评分下的跨片方向一致 %d/%d   符号检验 p = %.3f  （bin 评分下是 2/5, p = 1.000）"
          % (ok, n, p))
    for f in comp:
        print("  %-24s %s" % (f, "  ".join("%s %+.3f" % (x, agree[x][f]) for x in FILMS)))

    print()
    print("提醒：两套分都没有信度估计，所以这里的差异也可能只是重采样噪声。")


if __name__ == "__main__":
    main()
