-- Which steps are still done by hand?  An activity the batch jobs already
-- perform some of the time is a proven automation candidate: the system can
-- do it, it just is not always asked to.
CREATE OR REPLACE TABLE automation AS
SELECT
    activity,
    count(*)                                         AS n_events,
    count(*) FILTER (WHERE actor_type = 'human')     AS n_manual,
    count(*) FILTER (WHERE actor_type = 'batch')     AS n_batch,
    count(*) FILTER (WHERE actor_type = 'vendor')    AS n_vendor,
    avg((actor_type = 'human')::INT)                 AS manual_share,
    count(DISTINCT "user") FILTER (WHERE actor_type = 'human') AS n_people
FROM events
WHERE ts_valid
GROUP BY activity
ORDER BY n_manual DESC;
