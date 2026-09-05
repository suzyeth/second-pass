# Drive the demo through its beats on a fixed clock, so it can be recorded.
#
#   python record.py --plan          # print the timeline, change nothing
#   python record.py                 # run the choreography against Chrome
#
# Chrome must already be running with --remote-debugging-port=9222 on the live
# page. This connects over the DevTools Protocol and hands the page one script
# that performs every action itself, on absolute timestamps.
#
# Why the page drives itself rather than being clicked from here: every tool
# round-trip costs an unpredictable second or two, and the narration is fixed to
# the tenth of a second. A choreography evaluated inside the page keeps its own
# clock, so beat five starts at 116.3 seconds whatever the network did.
#
# The timings are not guesses. They are the measured lengths of the generated
# narration files, read off disk, so the picture and the voice cannot drift apart
# by an editing mistake.

import argparse
import asyncio
import glob
import json
import os
import urllib.request
import wave

import websockets

CDP = "http://127.0.0.1:9222"
NARRATION = "narration"


def beat_lengths():
    """Measured seconds per beat, in order. The video is cut to the voice."""
    out = []
    for path in sorted(glob.glob(os.path.join(NARRATION, "*.wav"))):
        with wave.open(path) as w:
            out.append((os.path.basename(path), w.getnframes() / w.getframerate()))
    return out


def timeline():
    starts, t = [], 0.0
    for name, secs in beat_lengths():
        starts.append((name, t, secs))
        t += secs
    return starts, t


# The choreography. One argument: the beat start times, so the page shares the
# clock the narration was cut on.
CHOREOGRAPHY = r"""
async (marks) => {
  const t0 = Date.now();
  const at = (s) => new Promise(r => {
    const wait = t0 + s * 1000 - Date.now();
    setTimeout(r, Math.max(0, wait));
  });
  const glide = (y) => window.scrollTo({ top: y, behavior: "smooth" });
  const el = (sel) => document.querySelector(sel);
  const top = (sel) => el(sel).getBoundingClientRect().top + window.scrollY;
  const log = [];
  const mark = (what) => log.push(((Date.now() - t0) / 1000).toFixed(1) + "  " + what);

  const [b1, b2, b3, b4, b5, b6] = marks;

  // 1 — the page, unhurried. The chart comes into view under the hook.
  mark("open on Tears of Steel");
  await at(b1 + 9);
  glide(top(".chart-wrap") - 140);

  // 2 — the false positive. Ask, let it answer, then go to the table it used.
  await at(b2);
  glide(0);
  await at(b2 + 2);
  document.querySelector("#presets button[data-i='0']").click();
  mark("asked: which features explain attention");
  await at(b2 + 16);
  glide(top("#prooftable") - 200);
  mark("correlation table");

  // 3 — where the numbers came from: the chip, the URL, the query rows.
  await at(b3);
  glide(0);
  mark("chip: model, mcp, clickhouse, cloud run revision");
  await at(b3 + 9);
  glide(top(".log") - 120);
  mark("the mcp run_query rows");

  // 4 — the negative control, and back again.
  await at(b4);
  glide(top("#prooftable") - 220);
  await at(b4 + 4);
  document.querySelector("#toggle button[data-shuffled='1']").click();
  mark("shuffled");
  await at(b4 + 26);
  document.querySelector("#toggle button[data-shuffled='0']").click();
  mark("back to real attention");

  // 5 — the third film, which is where the last pattern died.
  await at(b5);
  glide(top("#crossfilm") - 320);
  mark("cross-film sign test");
  await at(b5 + 14);
  glide(0);
  await at(b5 + 16);
  document.querySelector("#filmpick button[data-film='sintel']").click();
  mark("switch to Sintel");
  await at(b5 + 21);
  glide(top("#prooftable") - 200);
  mark("Sintel's table: nothing clears anything");

  // 6 — out.
  await at(b6);
  glide(0);
  mark("close");
  await at(b6 + marks.tail);

  return log;
}
"""


async def page_target():
    with urllib.request.urlopen(CDP + "/json", timeout=10) as r:
        targets = json.load(r)
    pages = [t for t in targets if t.get("type") == "page" and "run.app" in t.get("url", "")]
    if not pages:
        raise SystemExit("no Chrome tab on the deployed page — is Chrome running with "
                         "--remote-debugging-port=9222 ?")
    return pages[0]["webSocketDebuggerUrl"]


async def run():
    marks, total = timeline()
    starts = [s for _, s, _ in marks]
    tail = marks[-1][2]

    url = await page_target()
    async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
        # The marks array carries a .tail the choreography reads for its final hold.
        expr = (
            "(async () => { const m = " + json.dumps(starts) + "; m.tail = " + str(tail)
            + "; return await (" + CHOREOGRAPHY + ")(m); })()"
        )
        await ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expr, "awaitPromise": True, "returnByValue": True,
                       "timeout": int((total + 30) * 1000)},
        }))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                break

    result = msg.get("result", {}).get("result", {})
    if result.get("subtype") == "error" or "exceptionDetails" in msg.get("result", {}):
        raise SystemExit("choreography failed: " + json.dumps(msg)[:400])
    for line in result.get("value", []):
        print("  " + line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    args = ap.parse_args()

    marks, total = timeline()
    print("beat            starts     runs")
    for name, start, secs in marks:
        print("  %-16s %6.1fs  %5.1fs" % (name, start, secs))
    print("  %-16s %6s   %5.1fs  (cap 180)" % ("TOTAL", "", total))
    if args.plan:
        return
    print()
    asyncio.run(run())


if __name__ == "__main__":
    main()
