// Score every segment of one film. Built to run unattended for hours.
//
//   node score-film.js tos            # the 100-bin grid
//   node score-film.js tos --beats    # the merged-shot beats
//
// The free tier dies at roughly twenty inline-video calls, and this needs three
// hundred. That single fact shapes the whole design: quota exhaustion is the
// normal case here, not the error case. So a 429 is never a failure — it backs
// off and waits, indefinitely, because the alternative is a human babysitting a
// trickle. Progress is written after every clip and already-scored bins are
// skipped, so killing this at any moment costs at most one call.
//
// The prompt is blind on purpose. It never sees an attention value and is told
// not to guess whether viewers liked the clip, because a model that knows what
// answer would be convenient stops being a measurement.

const fs = require("fs");
const path = require("path");
const { GoogleGenAI } = require("@google/genai");

const MODEL = process.env.GEMINI_MODEL || "gemini-3.5-flash-lite";
const BASE_DELAY_MS = Number(process.env.SCORE_DELAY_MS || 8000);
const BACKOFF_MS = [60_000, 120_000, 300_000, 600_000, 900_000];

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

const SYSTEM = `You are a film analyst watching a short stretch of a film, with sound.
Describe only what is on screen and what is audible. Do not speculate about whether an
audience enjoyed it, rewatched it, or skipped it — you are not being asked to predict
that, and guessing would make your answer useless.`;

const ASK = `Rate this clip on six scales, 0 to 10, then describe it in one line.

- visual_event_density: how much visibly happens. 0 = a near-static frame; 10 = rapid
  action, cuts, or large movement.
- story_information: how much a first-time viewer would learn that matters to the plot.
  0 = nothing is established or revealed; 10 = a decisive plot beat.
- character_presence: how prominent human faces and performance are. 0 = no people;
  10 = a face filling the frame, acting.
- speech_density: how much intelligible dialogue is spoken. 0 = silence or noise only;
  10 = continuous speech.
- score_intensity: how present and driving the music or sound design is. 0 = near
  silence; 10 = loud, propulsive score.
- inertness: how little reason there is to keep watching THIS stretch specifically.
  0 = compelling; 10 = the viewer could skip it and lose nothing.

Return only JSON: {"visual_event_density":n,"story_information":n,
"character_presence":n,"speech_density":n,"score_intensity":n,"inertness":n,
"one_line":"..."}`;

const FIELDS = [
  "visual_event_density",
  "story_information",
  "character_presence",
  "speech_density",
  "score_intensity",
  "inertness",
];

const hasScores = (o) =>
  o && typeof o === "object" && FIELDS.every((f) => typeof o[f] === "number");

// Twice in a hundred clips, the model returned the six scores wrapped one level
// deeper - {"0": {...}} instead of {...}. Nothing threw. The row was written with
// the fields nested where nothing would look for them, the log printed
// "evt=undefined", and the run still reported 100/100. A scorer that reports
// success on output it did not understand is the exact failure this project
// exists to catch, so it now checks what it got: unwrap a single nested object
// if that is all that is wrong, and otherwise throw so the retry loop runs.
function normalise(parsed) {
  if (hasScores(parsed)) return parsed;

  const nested = Object.values(parsed || {}).filter(hasScores);
  if (nested.length === 1) return nested[0];

  throw new Error(
    `response has no score fields (keys: ${Object.keys(parsed || {}).join(",")})`,
  );
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stamp = () => new Date().toISOString().slice(11, 19);

async function scoreClip(file) {
  const data = fs.readFileSync(file).toString("base64");
  let quotaHits = 0;

  for (;;) {
    try {
      const response = await ai.models.generateContent({
        model: MODEL,
        contents: [
          {
            role: "user",
            parts: [{ inlineData: { mimeType: "video/mp4", data } }, { text: ASK }],
          },
        ],
        config: {
          systemInstruction: SYSTEM,
          temperature: 0,
          responseMimeType: "application/json",
        },
      });

      const text = response?.text;
      if (!text) throw new Error("no content");
      return normalise(
        JSON.parse(text.trim().replace(/^```(?:json)?\s*|\s*```$/g, "")),
      );
    } catch (error) {
      const status = error && typeof error.status === "number" ? error.status : null;

      if (status === 429) {
        const wait = BACKOFF_MS[Math.min(quotaHits, BACKOFF_MS.length - 1)];
        quotaHits += 1;
        process.stdout.write(`\n    [${stamp()}] quota — waiting ${wait / 60000}min `);
        await sleep(wait);
        continue;
      }

      // 5xx is the free tier shedding load, which it does hardest from cloud
      // egress but happens here too. Worth a few tries; 4xx is our own bug.
      if (status !== null && status >= 500 && quotaHits < 3) {
        quotaHits += 1;
        await sleep(15_000);
        continue;
      }

      // A response we could not read is a resampling problem, not a bug in the
      // request - the same prompt succeeded on the ninety-eight clips either
      // side of it. Worth asking again before giving up on the clip.
      if (status === null && /no score fields|no content/.test(error.message)
          && quotaHits < 3) {
        quotaHits += 1;
        process.stdout.write(`retrying (${error.message}) `);
        await sleep(5_000);
        continue;
      }
      throw error;
    }
  }
}

// Two units, one scorer. Bins come from YouTube's grid and straddle cuts; beats are
// unions of detected shots and do not. Comparing the two is the whole experiment, so
// they MUST go through the same prompt, the same model and the same resolution - a
// second copy of this file would drift on the first edit and quietly make the
// comparison meaningless.
const UNITS = {
  bin: {
    key: "bin",
    source: "bins.json",
    out: "scored.json",
    clips: (dir, i) => path.join(dir, "clips", `bin_${String(i).padStart(3, "0")}.mp4`),
  },
  beat: {
    key: "beat",
    source: "beats.json",
    out: "scored-beats.json",
    clips: (dir, i) =>
      path.join(dir, "beat_clips", `beat_${String(i).padStart(3, "0")}.mp4`),
  },
};

(async () => {
  const filmId = process.argv[2] || "tos";
  const unitName = process.argv.includes("--beats") ? "beat" : "bin";
  const unit = UNITS[unitName];
  const dir = path.join("films", filmId);
  const outFile = path.join(dir, unit.out);

  const bins = JSON.parse(fs.readFileSync(path.join(dir, unit.source), "utf8"));
  const done = fs.existsSync(outFile) ? JSON.parse(fs.readFileSync(outFile, "utf8")) : [];
  const byBin = new Map(done.filter((r) => !r.error).map((r) => [r[unit.key], r]));

  console.log(`${filmId}: ${bins.length} ${unitName}s, ${byBin.size} already scored`);
  const started = Date.now();
  let scored = 0;

  const results = [];
  for (const b of bins) {
    if (byBin.has(b[unit.key])) {
      results.push(byBin.get(b[unit.key]));
      continue;
    }

    const clip = unit.clips(dir, b[unit.key]);
    process.stdout.write(`[${stamp()}] ${unitName} ${String(b[unit.key]).padStart(3)} `);

    try {
      const s = await scoreClip(clip);
      results.push({ ...b, ...s });
      scored += 1;
      console.log(
        `evt=${s.visual_event_density} sto=${s.story_information} ` +
          `fac=${s.character_presence} tlk=${s.speech_density} ` +
          `mus=${s.score_intensity} inr=${s.inertness}  (${scored} this run)`,
      );
    } catch (error) {
      console.log(`FAILED ${String(error.message).slice(0, 70)}`);
      results.push({ ...b, error: String(error.message).slice(0, 200) });
    }

    fs.writeFileSync(outFile, JSON.stringify(results, null, 1));
    await sleep(BASE_DELAY_MS);
  }

  const ok = results.filter(hasScores).length;
  const mins = Math.round((Date.now() - started) / 60000);
  console.log(`\n${filmId}: ${ok}/${bins.length} scored, ${scored} this run, ${mins} min`);
})();
