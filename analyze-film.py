# Full-population analysis for one film: every bin, not just the extremes.
#
#   python analyze-film.py tos
#
# The day-1 gate compared 12 low-residual bins against 12 high ones because that
# was all the quota allowed. With every bin scored the contrast is no longer
# necessary and no longer honest — a correlation over all 100 is the real test,
# and it is the one that can be significant at this n.
#
# Two controls are reported alongside, because either one being large would mean
# the headline number is measuring something other than content:
#
#   position   Spearman of the field against bin index. A field that just tracks
#              "how far into the film is this" explains nothing about content.
#   raw        Spearman against RAW attention rather than the residual. If a
#              field correlates with raw but not residual, it is riding the
#              positional trend, which is exactly the false GO this design
#              exists to avoid.

import io
import json
import sys

FIELDS = [
    "visual_event_density",
    "story_information",
    "character_presence",
    "speech_density",
    "score_intensity",
    "inertness",
]

EXPECTED = {f: (-1 if f == "inertness" else 1) for f in FIELDS}


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        # Average ranks over ties — these are 0-10 integer scores, so ties are
        # the norm and ignoring them inflates the correlation.
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (sum((rx[i] - mx) ** 2 for i in range(n)) * sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / den if den else 0.0


def run(film_id, exclude_edges):
    rows = [
        r
        for r in json.load(io.open(f"films/{film_id}/scored.json", encoding="utf-8"))
        if "inertness" in r and (not exclude_edges or not r.get("edge"))
    ]
    n = len(rows)

    # Two floors, because there are two different questions, and this script used
    # to report only the first — which made it disagree with the README and with
    # the agent about how many features survive.
    #
    #   one_test   1.96, the single-test threshold: "is THIS feature related to
    #              attention", asked about one feature decided on in advance.
    #   crit       2.6383, the same threshold with Bonferroni applied over the six
    #              features: "did we find ANYTHING", which is the question actually
    #              being asked when all six are printed at once — as they are here.
    #
    # The verdict uses the corrected floor because this table shows all six.
    # Judging six results against a one-test floor is how a null gets reported as
    # a finding. The constants match `correlation_table` in agent.py exactly, so
    # the repro script and the live agent cannot drift apart.
    one_test = 1.96 / (n - 1) ** 0.5
    crit = 2.6383 / (n - 1) ** 0.5

    label = "excluding edge bins" if exclude_edges else "all bins"
    print(f"\n=== {film_id} — {label} — n={n} ===")
    print(f"{'field':<24}{'vs residual':>12}{'vs raw':>9}{'vs position':>13}   verdict")

    hits = []
    for f in FIELDS:
        vals = [r[f] for r in rows]
        # A feature that never varies has no correlation. Printing +0.000 for it
        # claims a measurement that was never possible - Big Buck Bunny has no
        # dialogue, so speech_density is a column of identical zeroes. The agent
        # returns NULL and a verdict of undefined for exactly this case, and a repro
        # script the README invites judges to run must not say something different.
        if len(set(vals)) < 2:
            print(f"{f:<24}{'n/a':>12}{'n/a':>9}{'n/a':>13}   undefined - does not vary")
            continue
        
        r_res = spearman(vals, [r["res"] for r in rows])
        r_raw = spearman(vals, [r["raw"] for r in rows])
        r_pos = spearman(vals, [r["bin"] for r in rows])

        sig = abs(r_res) > crit
        weak = abs(r_res) > one_test
        right = r_res * EXPECTED[f] > 0

        # A feature between the two floors is not nothing and not a finding. It is
        # named as what it is rather than rounded to either neighbour.
        if sig and right:
            verdict = "SIGNIFICANT"
            hits.append((f, r_res))
        elif sig:
            verdict = "sig, WRONG WAY"
        elif weak and right:
            verdict = "uncorrected only"
        elif weak:
            verdict = "uncorrected, WRONG WAY"
        else:
            verdict = ""

        print(f"{f:<24}{r_res:+12.3f}{r_raw:+9.3f}{r_pos:+13.3f}   {verdict}")

    print(f"\n|rho| > {crit:.3f} is p<0.05 at n={n}, corrected for testing all "
          f"{len(FIELDS)} features")
    print(f"|rho| > {one_test:.3f} is the uncorrected single-test floor — shown "
          f"because it is what a one-feature question would use, not what this "
          f"table is entitled to")
    if hits:
        print("SIGNAL:", ", ".join(f"{f} ({r:+.3f})" for f, r in hits))
    else:
        print("NO SIGNAL: nothing clears the corrected threshold in the expected "
              "direction")


film = sys.argv[1] if len(sys.argv) > 1 else "tos"
run(film, exclude_edges=False)
run(film, exclude_edges=True)
