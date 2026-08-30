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
    -- How much of this film's attention is explained by position alone. The
    -- ratio of second-half to first-half mean attention: 4.4 on Tears of Steel,
    -- 1.9 on Sintel. Stored because it is the confound every naive analysis of
    -- this data walks into, and a query should be able to select for it.
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
