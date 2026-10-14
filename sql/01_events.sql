-- Clean event table.
-- One row per logged step.  Timestamps outside the plausible window are kept
-- but flagged, so the data-quality report can count them; every downstream
-- query filters on ts_valid.
CREATE OR REPLACE TABLE events AS
WITH raw AS (
    SELECT
        case_id,
        event_idx,
        activity,
        CAST(replace(ts_raw, 'Z', '') AS TIMESTAMP) AS ts,
        "user",
        net_worth_eur
    FROM read_parquet('{events_parquet}')
)
SELECT
    *,
    ts >= TIMESTAMP '{valid_ts_start}' AND ts < TIMESTAMP '{valid_ts_end}' AS ts_valid,
    CASE
        WHEN "user" = '{vendor_user}'          THEN 'vendor'
        WHEN "user" LIKE '{batch_prefix}%'     THEN 'batch'
        ELSE 'human'
    END AS actor_type
FROM raw;

CREATE OR REPLACE TABLE cases AS
SELECT * FROM read_parquet('{cases_parquet}');
