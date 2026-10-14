-- Weekly manual workload: the number of steps people performed, by week.
-- This is what an operations lead staffs against.
CREATE OR REPLACE TABLE weekly_workload AS
SELECT
    date_trunc('week', ts)                                       AS week,
    count(*) FILTER (WHERE actor_type = 'human')                 AS manual_events,
    count(*) FILTER (WHERE actor_type = 'human'
                     AND activity = 'Record Invoice Receipt')    AS manual_invoices,
    count(*) FILTER (WHERE activity = 'Create Purchase Order Item') AS po_lines_created,
    count(DISTINCT "user") FILTER (WHERE actor_type = 'human')   AS active_people
FROM events
WHERE ts_valid
GROUP BY 1
ORDER BY 1;
