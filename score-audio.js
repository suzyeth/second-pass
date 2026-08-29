// GO/KILL gate, second attempt: same 24 position-matched clips, WITH sound.
//
// The first run scored silent clips and found nothing (four scores all pointed
// the right way, none by a meaningful margin, and two Spearman correlations came
// back with the wrong sign). That is weak evidence against the thesis, but the
// test had a hole in it: for a film like Tears of Steel, dialogue and score are
// probably the larger part of why a stretch holds attention, and they had been
// stripped out to save quota. Rejecting an attention hypothesis using silent
// clips is not a clean rejection.
//
// So: same clips, same blind prompt discipline, audio restored, plus two scores
// that only exist because there is now sound to hear.
//
// Two operational changes, both learned from the first run losing 7 of 24
// results to a 429: progress is written after every clip and already-scored
// bins are skipped on a rerun, and quota errors back off and retry rather than
// being recorded as failures.

const fs = require("fs");
const path = require("path");
const { GoogleGenAI } = require("@google/genai");

const MODEL = process.env.GEMINI_MODEL || "gemini-3.5-flash-lite";
const OUT = "scored-audio.json";
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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function scoreClip(file) {
  const data = fs.readFileSync(file).toString("base64");

  for (let attempt = 1; ; attempt += 1) {
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
      return JSON.parse(text.trim().replace(/^```(?:json)?\s*|\s*```$/g, ""));
    } catch (error) {
      const status = error && typeof error.status === "number" ? error.status : null;
      // 429 here is a rate limit rather than depleted credit — the first run hit
      // it after 17 video calls and recovered. Worth waiting out; 4xx otherwise
      // is a real error and fails fast.
      if (status === 429 && attempt <= 6) {
        const wait = 20_000 * attempt;
        process.stdout.write(`[429, waiting ${wait / 1000}s] `);
        await sleep(wait);
        continue;
      }
      throw error;
    }
  }
}

(async () => {
  const segments = JSON.parse(fs.readFileSync("segments.json", "utf8"));
  const done = fs.existsSync(OUT) ? JSON.parse(fs.readFileSync(OUT, "utf8")) : [];
  const byBin = new Map(done.filter((r) => !r.error).map((r) => [r.bin, r]));

  const out = [];
  for (const seg of segments) {
    if (byBin.has(seg.bin)) {
      out.push(byBin.get(seg.bin));
      continue;
    }

    const file = path.join("clips_audio", `bin_${seg.bin}.mp4`);
    process.stdout.write(`bin ${String(seg.bin).padStart(2)} (${seg.group.padEnd(4)}) ... `);
    try {
      const scored = await scoreClip(file);
      out.push({ ...seg, ...scored });
      console.log(
        `evt=${scored.visual_event_density} story=${scored.story_information} ` +
          `face=${scored.character_presence} talk=${scored.speech_density} ` +
          `mus=${scored.score_intensity} inert=${scored.inertness}`,
      );
    } catch (error) {
      console.log(`FAILED: ${String(error.message).slice(0, 80)}`);
      out.push({ ...seg, error: String(error.message).slice(0, 200) });
    }

    fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
    await sleep(1500);
  }

  const ok = out.filter((r) => !r.error).length;
  console.log(`\nscored ${ok}/${out.length} -> ${OUT}`);
})();
