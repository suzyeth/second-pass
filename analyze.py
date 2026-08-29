# Reproduces the day-1 gate result from scored-audio.json.
#
# The point of keeping this runnable rather than pasting numbers into a doc: the
# finding is a negative one, and a negative finding is only worth anything if
# someone can re-run it and get the same answer.
#
#   python analyze.py

import json
import statistics as st

FIELDS = [
    "visual_event_density",
    "story_information",
    "character_presence",
    "speech_density",
    "score_intensity",
    "inertness",
]

# Higher attention should mean MORE of these, except inertness, where the whole
# point of the word is that more of it should mean less attention.
EXPECTED_SIGN = {f: (-1 if f == "inertness" else 1) for f in FIELDS}


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0] * len(v)
        for k, i in enumerate(order):
            out[i] = k
        return out

    rx, ry = rank(x), rank(y)
    n = len(x)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1))


def report(rows, label):
    low = [r for r in rows if r["group"] == "LOW"]
    high = [r for r in rows if r["group"] == "HIGH"]
    n = len(rows)
    # Rough two-sided 5% critical value for Spearman: 1.96/sqrt(n-1).
    crit = 1.96 / (n - 1) ** 0.5

    print(f"\n=== {label} ===")
    print(f"n={n}  (LOW {len(low)} / HIGH {len(high)})")
    print(
        f"mean bin index: LOW {st.mean([r['bin'] for r in low]):.1f}  "
        f"HIGH {st.mean([r['bin'] for r in high]):.1f}   "
        "<- these must be close, or the sample is testing position, not content"
    )
    print(f"\n{'field':<24}{'LOW':>6}{'HIGH':>7}{'diff':>7}{'rho':>8}   sign")
    for f in FIELDS:
        a = [r[f] for r in low]
        b = [r[f] for r in high]
        diff = st.mean(b) - st.mean(a)
        rho = spearman([r[f] for r in rows], [r["res"] for r in rows])
        agrees = "ok" if diff * EXPECTED_SIGN[f] > 0 else "WRONG WAY"
        print(f"{f:<24}{st.mean(a):6.2f}{st.mean(b):7.2f}{diff:+7.2f}{rho:+8.3f}   {agrees}")

    best = max(abs(spearman([r[f] for r in rows], [r["res"] for r in rows])) for f in FIELDS)
    print(f"\nstrongest |rho| = {best:.3f}   needs > {crit:.3f} for p<0.05")
    print("VERDICT:", "signal" if best > crit else "no detectable signal at this n")


rows = [r for r in json.load(open("scored-audio.json")) if "error" not in r]

report(rows, "all 24 segments")

# Bins near either end have a one-sided moving-average baseline, so their
# residual is an artifact of the window rather than a measurement. Three of them
# are title or credit cards, and they landed in BOTH groups — which is the
# clearest evidence available that the edges are noise.
report([r for r in rows if 5 <= r["bin"] <= 88], "excluding edges (bin 5..88)")
