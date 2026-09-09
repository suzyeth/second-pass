-- Second Pass — ClickHouse schema
--
-- Two tables, and the split between them is the whole argument for using a
-- columnar database rather than a spreadsheet.
--
--   segments         ~100 rows per film. Real, measured, small.
--   playback_events  millions of rows per film. Synthetic, and honest about it.
--
-- The interesting questions are joins across the two: "in the stretches where
-- viewers actually dropped out, what was on screen?" is one query over a wide
-- fact table grouped against a narrow dimension table, and that is the shape
-- ClickHouse exists for. Answering it over 300 rows would prove nothing.
--
-- Why synthetic events at all: the only real attention signal available for a
-- public film is YouTube's most-replayed heatmap — 100 buckets, rewatch
-- intensity, no per-viewer anything. Per-viewer events are what a platform
-- owner has and an outsider never does. So they are generated from the real
-- curve, labelled synthetic in the schema and in the UI, and used only to
-- demonstrate the workload. Every claim about a film comes from `segments`.

CREATE TABLE IF NOT EXISTS films
(
    film            LowCardinality(String),
    title           String,
    youtube_id      String,
    duration_s      Float32,
    bin_count       UInt16,
    -- How much of this film's attention is explained by position alone: the
    -- ratio of second-half to first-half mean attention. 4.40 on Tears of Steel,
    -- 3.00 on Big Buck Bunny, both over 100 scored segments. Stored because it is
    -- the confound every naive analysis of this data walks into, and a query
    -- should be able to select for it.
    position_bias   Float32
)
ENGINE = MergeTree
ORDER BY film;


CREATE TABLE IF NOT EXISTS segments
(
    film                    LowCardinality(String),
    bin                     UInt16,
    start_s                 Float32,
    end_s                   Float32,

    -- REAL. YouTube most-replayed, min-max normalised per film by YouTube.
    attention_raw           Float32,
    -- What this POSITION scores, as a local moving average (+-8 bins).
    attention_base          Float32,
    -- raw - base. The only column that means "watched more than expected".
    -- Correlating content against attention_raw instead of this reproduces the
    -- positional trend and returns significant, confident, wrong answers.
    attention_res           Float32,
    -- Bins near either end have a one-sided baseline, so their residual is an
    -- artifact of the window. Excluded from claims, kept in the table.
    is_edge                 UInt8,

    -- The 100-bin grid covers the whole YouTube upload, and the upload ends with
    -- credits: 588s in on Tears of Steel, 489.5s on Big Buck Bunny, 744s on
    -- Sintel, pinned by frame sampling. Roughly a fifth of each upload, and not
    -- film. Tears of Steel also has a post-credits scene at ~711-734s which IS
    -- film, but three bins cut off from the body by 123s of credits have no
    -- valid local baseline, so they are excluded with the rest.
    --
    -- Leaving them in did two things. It put the upload's maximum attention -
    -- the post-credits stinger, which people jump to - inside the film, which is
    -- where position_bias 4.40 came from when the film's own figure is 1.28. And
    -- because attention_base is a moving average, the window for the last stretch
    -- of FILM reached into a region eight times higher, so those residuals were
    -- an artifact of data that is not film. Filtering the rows out afterwards
    -- does not undo that. prepare.py truncates the curve BEFORE detrending.
    --
    -- A bin belongs to the credits when its MIDPOINT does. Keying on its start
    -- keeps a bin that is 94 percent credits, and on Big Buck Bunny that single
    -- segment moved a feature across the significance floor and back.
    --
    -- Credit bins are also flagged is_edge. The two mean different things, but
    -- every query filters on is_edge = 0, so this makes the exclusion fail-safe
    -- rather than dependent on each of them being found and updated.
    is_credits              UInt8,

    -- CONTENT, scored by Gemini from the clip itself, blind: the prompt never
    -- sees an attention value and is told not to guess what viewers preferred.
    visual_event_density    UInt8,
    story_information       UInt8,
    character_presence      UInt8,
    speech_density          UInt8,
    score_intensity         UInt8,
    inertness               UInt8,
    one_line                String,

    scored_model            LowCardinality(String),
    scored_at               DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (film, bin);


CREATE TABLE IF NOT EXISTS playback_events
(
    film        LowCardinality(String),
    session_id  UInt32,
    -- Whole second into the film. Joined to segments by bin, not by timestamp,
    -- because the bin edges come from YouTube and recomputing them from a
    -- rounded duration drifts by up to a bin width by the end of a film.
    t_s         UInt16,
    bin         UInt16,
    event       Enum8('watch' = 1, 'seek_back' = 2, 'exit' = 3),
    ts          DateTime
)
ENGINE = MergeTree
PARTITION BY film
ORDER BY (film, bin, session_id);


-- The second corpus, and it answers a different question from the first.
--
-- 73 real hackathon demo videos pulled from Devpost on 2026-09-05, 56 of them with
-- English auto-captions. `segments` is about one film's audience; this is about how
-- people who make demo videos actually make them - how long, how fast, when the
-- demo starts, what the first fifteen seconds do.
--
-- THESE ARE DESCRIPTIVE MEDIANS, NOT A STANDARD. The corpus was sampled in two
-- batches, winners and non-winners of the same events, and the difference between
-- them is weak and confounded: median duration 174s against 175s, and the winners'
-- faster speech tracks their being more often teams (44% vs 23% multi-voice) rather
-- than anything about pace. The batches are pooled and `batch` is kept as
-- provenance only. Anything reading this table must return a percentile AND the
-- spread, never a pass or a fail - only 32% of the corpus falls in the 2-3 minute
-- band, so a median here is a default, not a threshold. A competition's own written
-- duration cap is a rule and may fail a build; a median from this table may not.
--
-- Nullable throughout because 17 of the 73 have no captions. A video with no
-- transcript has no words-per-minute - not zero words per minute.
CREATE TABLE IF NOT EXISTS craft_videos
(
    video_id        String,
    batch           LowCardinality(String),
    duration_s      UInt32,
    has_captions    UInt8,

    words_total     Nullable(UInt32),
    wpm             Nullable(Float32),
    sentence_words  Nullable(Float32),

    words_first_5s  Nullable(UInt16),
    words_first_10s Nullable(UInt16),
    words_first_15s Nullable(UInt16),
    words_first_20s Nullable(UInt16),

    intro_first_15s          Nullable(UInt8),
    names_project_first_15s  Nullable(UInt8),
    problem_first_15s        Nullable(UInt8),

    demo_verb_at_s  Nullable(Float32),
    demo_verb_frac  Nullable(Float32),
    tech_first_frac Nullable(Float32),

    mentions_number Nullable(UInt8),
    multi_voice     Nullable(UInt8),

    thanks_at_end   Nullable(UInt8),
    cta_at_end      Nullable(UInt8),
    tail_wpm        Nullable(Float32),
    silent_tail     Nullable(UInt8)
)
ENGINE = MergeTree
ORDER BY video_id;
