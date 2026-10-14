-- One row per PO line that was invoiced: when its FIRST invoice was recorded,
-- when its first clearance (payment) happened, and whether that took longer
-- than the threshold.
--
-- The log does not link a payment to the invoice it settles.  On a line with
-- one invoice that does not matter; on a line with 200 (framework orders), a
-- single missing clearance shifts every later pair, and the "delay" becomes a
-- pairing artefact.  So the unit is the line's first invoice: well defined on
-- every line, and the k-th pairing is kept only to compute it.
--
-- Censoring: an invoice recorded less than the threshold before the log ends
-- has an unknown outcome *whether or not it has cleared* - keeping only the
-- ones that cleared fast would bias the late rate down.  Those invoices are
-- excluded from every rate (is_late IS NULL).  Older invoices still open at
-- the end of the log are late: they have already passed the threshold.
CREATE OR REPLACE TABLE invoices AS
WITH ev AS (
    SELECT * FROM events WHERE ts_valid
),
log_end AS (
    SELECT max(ts) AS log_end FROM ev
),
-- "Cumulative net worth" is the value of the whole PO line, repeated on every
-- event; it is not the amount of an individual invoice.  A line's value is
-- therefore spread evenly over its invoices - an approximation, but one that
-- does not count a 200-invoice line's value 200 times.
rec AS (
    SELECT case_id, ts AS recorded_at,
           net_worth_eur / count(*) OVER (PARTITION BY case_id) AS invoice_value_eur,
           "user" AS recorded_by,
           row_number() OVER (PARTITION BY case_id ORDER BY ts, event_idx) AS k
    FROM ev WHERE activity = 'Record Invoice Receipt'
),
clr AS (
    SELECT case_id, ts AS cleared_at,
           row_number() OVER (PARTITION BY case_id ORDER BY ts, event_idx) AS k
    FROM ev WHERE activity = 'Clear Invoice'
),
paired AS (
    SELECT r.*, c.cleared_at, l.log_end,
           count(*) OVER (PARTITION BY r.case_id) AS n_invoices_on_line
    FROM rec r
    LEFT JOIN clr c USING (case_id, k)
    CROSS JOIN log_end l
),
-- Rework that had already happened when the invoice was recorded - the only
-- rework a model scoring that invoice is allowed to see.
rework_before AS (
    SELECT r.case_id, r.k, count(e.case_id) AS n_rework_before
    FROM rec r
    LEFT JOIN ev e
      ON e.case_id = r.case_id
     AND e.ts <= r.recorded_at
     AND e.activity IN ({rework_list})
    GROUP BY r.case_id, r.k
)
SELECT
    p.case_id,
    p.n_invoices_on_line,
    p.recorded_at,
    p.cleared_at,
    p.invoice_value_eur,
    p.recorded_by LIKE '{batch_prefix}%' AS recorded_by_batch,
    date_diff('hour', p.recorded_at, p.cleared_at) / 24.0                       AS days_to_clear,
    date_diff('hour', p.recorded_at, coalesce(p.cleared_at, p.log_end)) / 24.0  AS days_open,
    date_diff('hour', p.recorded_at, p.log_end) / 24.0                          AS days_observable,
    p.cleared_at IS NULL                                                        AS still_open,
    -- A clearance dated before its invoice means the FIFO pairing failed on
    -- this line (typically a cancelled invoice shifting the queue).
    coalesce(p.cleared_at >= p.recorded_at, TRUE)                               AS pairing_ok,
    CASE
        WHEN p.cleared_at < p.recorded_at
            THEN NULL                                 -- pairing failed: unknown
        WHEN date_diff('hour', p.recorded_at, p.log_end) / 24.0 <= {late_days}
            THEN NULL                                 -- censored: outcome unknown
        WHEN p.cleared_at IS NOT NULL
            THEN date_diff('hour', p.recorded_at, p.cleared_at) / 24.0 > {late_days}
        ELSE TRUE                                     -- open past the threshold
    END AS is_late,
    rb.n_rework_before,
    -- The goods-receipt state at the moment the invoice was recorded.  Known
    -- at that moment, so it is a legitimate model input.
    cf.first_goods_receipt IS NOT NULL
        AND cf.first_goods_receipt <= p.recorded_at                            AS gr_before_invoice,
    date_diff('hour', cf.po_created, p.recorded_at) / 24.0                      AS po_age_at_invoice_days,
    cf.item_category, cf.document_type, cf.spend_area, cf.sub_spend_area,
    cf.vendor, cf.company, cf.item_type, cf.gr_based_iv, cf.goods_receipt_required,
    cf.po_value_eur
FROM paired p
JOIN rework_before rb USING (case_id, k)
JOIN case_facts cf USING (case_id)
WHERE p.k = 1;
