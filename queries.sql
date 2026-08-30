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

-- Q2 — the false positive, in one query. rankCorr against RAW attention says
-- faces drive viewers away. Against the residual it says almost nothing. The
-- difference is position, and position is what nobody controls for.
SELECT 'character_presence' AS field,
       round(rankCorr(character_presence, attention_raw), 3) AS vs_raw,
       round(rankCorr(character_presence, attention_res), 3) AS vs_residual,
       round(rankCorr(character_presence, bin), 3)           AS vs_position
FROM segments WHERE film = 'tos' AND is_edge = 0
UNION ALL
SELECT 'score_intensity',
       round(rankCorr(score_intensity, attention_raw), 3),
       round(rankCorr(score_intensity, attention_res), 3),
       round(rankCorr(score_intensity, bin), 3)
FROM segments WHERE film = 'tos' AND is_edge = 0
UNION ALL
SELECT 'visual_event_density',
       round(rankCorr(visual_event_density, attention_raw), 3),
       round(rankCorr(visual_event_density, attention_res), 3),
       round(rankCorr(visual_event_density, bin), 3)
FROM segments WHERE film = 'tos' AND is_edge = 0;

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
