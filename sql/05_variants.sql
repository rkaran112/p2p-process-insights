-- Process variants: the exact sequence of steps each PO line went through.
-- Consecutive repeats are collapsed ("Record Goods Receipt" x12 counts once),
-- so a monthly delivery schedule does not look like 12 different processes.
CREATE OR REPLACE TABLE variants AS
WITH ordered AS (
    SELECT
        case_id, activity, ts, event_idx,
        lag(activity) OVER (PARTITION BY case_id ORDER BY ts, event_idx) AS prev_activity
    FROM events
    WHERE ts_valid
),
collapsed AS (
    SELECT case_id,
           string_agg(activity, ' > ' ORDER BY ts, event_idx) AS variant
    FROM ordered
    WHERE prev_activity IS DISTINCT FROM activity
    GROUP BY case_id
)
SELECT
    variant,
    count(*)                                         AS n_cases,
    count(*) / sum(count(*)) OVER ()                 AS share,
    sum(count(*)) OVER (ORDER BY count(*) DESC, variant
                        ROWS UNBOUNDED PRECEDING)
        / sum(count(*)) OVER ()                      AS cumulative_share,
    row_number() OVER (ORDER BY count(*) DESC, variant) AS rank
FROM collapsed
GROUP BY variant
ORDER BY n_cases DESC;
