-- Where the days go: every step-to-next-step hand-off inside a case, with how
-- long the case waited between them.  Total waiting days, not the median,
-- ranks the bottlenecks - a slow hand-off that happens twice matters less
-- than a moderate one that happens 200,000 times.
CREATE OR REPLACE TABLE transitions AS
WITH ordered AS (
    SELECT
        case_id, activity, ts,
        lead(activity) OVER w AS next_activity,
        lead(ts)       OVER w AS next_ts
    FROM events
    WHERE ts_valid
    WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_idx)
)
SELECT
    activity                                       AS from_activity,
    next_activity                                  AS to_activity,
    count(*)                                       AS n,
    median(date_diff('hour', ts, next_ts) / 24.0)  AS median_days,
    quantile_cont(date_diff('hour', ts, next_ts) / 24.0, 0.9) AS p90_days,
    sum(date_diff('hour', ts, next_ts) / 24.0)     AS total_days
FROM ordered
WHERE next_activity IS NOT NULL
GROUP BY ALL;

CREATE OR REPLACE TABLE transitions AS
SELECT
    *,
    total_days / sum(total_days) OVER ()           AS share_of_all_waiting
FROM transitions
ORDER BY total_days DESC;
