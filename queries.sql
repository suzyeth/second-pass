-- The questions this database exists to answer.
--
-- Q1 is the product. Q2 is the reason the product has to exist: the same data,
-- asked the naive way, returns a confident wrong answer.

-- Q1 — which stretches were watched less than their position predicts, and what
-- was on screen there? The join the whole project is about.
SELECT bin, round(attention_res, 3) AS underperformance,
       visual_event_density AS evt, speech_density AS talk, one_line
FROM segments
WHERE film = 'tos' AND is_edge = 0
ORDER BY attention_res ASC
LIMIT 5;

-- Q2 — the false positive, in one query. Against RAW attention, faces drive
-- viewers away and a loud score keeps them. Against the residual both say almost
-- nothing. The difference is position, and position is what nobody controls for.
--
-- Spearman is computed here as a Pearson correlation of AVERAGE ranks rather than
-- with rankCorr, which does not average ties. The content scores take seven to
-- nine distinct values across eighty-four segments, so ties are most of the data
-- and the two disagree by up to 0.083 — enough to move a field across the
-- significance floor. At n=84 that floor is 0.215 for a single test, 0.290 once
-- corrected for testing six fields at once.
SELECT 'character_presence' AS field,
       round(corr(r_f, r_raw), 3)  AS vs_raw,
       round(corr(r_f, r_res), 3)  AS vs_residual,
       round(corr(r_f, r_pos), 3)  AS vs_position
FROM (SELECT rank() OVER (ORDER BY character_presence) + (count() OVER (PARTITION BY character_presence) - 1) / 2 AS r_f,
             rank() OVER (ORDER BY attention_raw) + (count() OVER (PARTITION BY attention_raw) - 1) / 2 AS r_raw,
             rank() OVER (ORDER BY attention_res) + (count() OVER (PARTITION BY attention_res) - 1) / 2 AS r_res,
             rank() OVER (ORDER BY bin)           + (count() OVER (PARTITION BY bin) - 1) / 2           AS r_pos
      FROM segments WHERE film = 'tos' AND is_edge = 0)
UNION ALL
SELECT 'score_intensity',
       round(corr(r_f, r_raw), 3),
       round(corr(r_f, r_res), 3),
       round(corr(r_f, r_pos), 3)
FROM (SELECT rank() OVER (ORDER BY score_intensity) + (count() OVER (PARTITION BY score_intensity) - 1) / 2 AS r_f,
             rank() OVER (ORDER BY attention_raw) + (count() OVER (PARTITION BY attention_raw) - 1) / 2 AS r_raw,
             rank() OVER (ORDER BY attention_res) + (count() OVER (PARTITION BY attention_res) - 1) / 2 AS r_res,
             rank() OVER (ORDER BY bin)           + (count() OVER (PARTITION BY bin) - 1) / 2           AS r_pos
      FROM segments WHERE film = 'tos' AND is_edge = 0)
UNION ALL
SELECT 'visual_event_density',
       round(corr(r_f, r_raw), 3),
       round(corr(r_f, r_res), 3),
       round(corr(r_f, r_pos), 3)
FROM (SELECT rank() OVER (ORDER BY visual_event_density) + (count() OVER (PARTITION BY visual_event_density) - 1) / 2 AS r_f,
             rank() OVER (ORDER BY attention_raw) + (count() OVER (PARTITION BY attention_raw) - 1) / 2 AS r_raw,
             rank() OVER (ORDER BY attention_res) + (count() OVER (PARTITION BY attention_res) - 1) / 2 AS r_res,
             rank() OVER (ORDER BY bin)           + (count() OVER (PARTITION BY bin) - 1) / 2           AS r_pos
      FROM segments WHERE film = 'tos' AND is_edge = 0);

-- Q3 — the scale join: where do sessions actually leave, and what is playing
-- there? 4.2M events aggregated against 95 dimension rows.
SELECT e.bin, count() AS exits,
       s.visual_event_density AS evt, s.inertness AS inert,
       substring(s.one_line, 1, 46) AS what
FROM playback_events AS e
INNER JOIN segments AS s ON e.film = s.film AND e.bin = s.bin
WHERE e.film = 'tos' AND e.event = 'exit' AND s.is_edge = 0
GROUP BY e.bin, s.visual_event_density, s.inertness, s.one_line
ORDER BY exits DESC
LIMIT 5;
