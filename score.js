// GO/KILL gate: does what is ON SCREEN explain attention, once position is
// controlled for?
//
// The 24 clips are position-matched by construction (see segments.json — they
// are the extremes of the attention RESIDUAL against a local moving-average
// baseline, not of raw attention, because raw attention rises monotonically
// toward the end of this film and any position-correlated feature would look
// predictive).
//
// The prompt is deliberately blind: it never sees an attention value, and it is
// told not to guess whether viewers liked the clip. It is asked only to describe
// what is on screen. If the two groups do not separate on any of these scores,
// the thesis does not hold and the plan says so on day 1 rather than day 9.
//
// Clips are silent (extracted with -an). Visual-only is the cheaper first test;
// if the signal is absent here, audio is the next thing to add, not the reason
// to give up.

const fs = require("fs");
const path = require("path");
const { GoogleGenAI } = require("@google/genai");

const MODEL = process.env.GEMINI_MODEL || "gemini-3.5-flash-lite";
const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

const SYSTEM = `You are a film analyst watching a short silent stretch of a film.
Describe only what is visibly on screen. Do not speculate about whether an audience
enjoyed it, rewatched it, or skipped it — you are not being asked to predict that,
and guessing would make your answer useless.`;

const ASK = `Rate this clip on four scales, 0 to 10, then describe it in one line.

- visual_event_density: how much visibly happens. 0 = a near-static frame; 10 = rapid
  action, cuts, or large movement.
- story_information: how much a first-time viewer would learn that matters to the plot.
  0 = nothing is established or revealed; 10 = a decisive plot beat.
- character_presence: how prominent human faces and performance are. 0 = no people;
  10 = a face filling the frame, acting.
- inertness: how little reason there is to keep watching THIS stretch specifically.
  0 = compelling; 10 = the viewer could skip it and lose nothing.

Return only JSON: {"visual_event_density":n,"story_information":n,
"character_presence":n,"inertness":n,"one_line":"..."}`;

async function scoreClip(file) {
  const data = fs.readFileSync(file).toString("base64");
  const response = await ai.models.generateContent({
    model: MODEL,
    contents: [
      {
        role: "user",
        parts: [
          { inlineData: { mimeType: "video/mp4", data } },
          { text: ASK },
        ],
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
  const cleaned = text.trim().replace(/^```(?:json)?\s*|\s*```$/g, "");
  return JSON.parse(cleaned);
}

(async () => {
  const segments = JSON.parse(fs.readFileSync("segments.json", "utf8"));
  const out = [];

  for (const seg of segments) {
    const file = path.join("clips", `bin_${seg.bin}.mp4`);
    process.stdout.write(`bin ${String(seg.bin).padStart(2)} (${seg.group.padEnd(4)}) ... `);
    try {
      const scored = await scoreClip(file);
      out.push({ ...seg, ...scored });
      console.log(
        `evt=${scored.visual_event_density} story=${scored.story_information} ` +
          `face=${scored.character_presence} inert=${scored.inertness}  ${String(scored.one_line).slice(0, 52)}`,
      );
    } catch (error) {
      console.log(`FAILED: ${String(error.message).slice(0, 90)}`);
      out.push({ ...seg, error: String(error.message).slice(0, 200) });
    }
    await new Promise((r) => setTimeout(r, 700));
  }

  fs.writeFileSync("scored.json", JSON.stringify(out, null, 1));
  const ok = out.filter((r) => !r.error).length;
  console.log(`\nscored ${ok}/${out.length} -> scored.json`);
})();
