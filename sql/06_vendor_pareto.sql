-- Is late payment spread evenly, or concentrated in a few vendors?
-- Censored invoices (is_late IS NULL) are excluded from every count.
CREATE OR REPLACE TABLE vendor_pareto AS
WITH v AS (
    SELECT
        vendor,
        any_value(spend_area)                        AS main_spend_area,
        count(*)                                     AS n_invoices,
        count(*) FILTER (WHERE is_late)              AS n_late,
        avg(is_late::INT)                            AS late_rate,
        median(days_to_clear)                        AS median_days_to_clear,
        sum(invoice_value_eur) FILTER (WHERE is_late) AS late_value_eur
    FROM invoices
    WHERE is_late IS NOT NULL
    GROUP BY vendor
)
SELECT
    *,
    row_number() OVER (ORDER BY n_late DESC, vendor)                    AS late_rank,
    sum(n_late) OVER (ORDER BY n_late DESC, vendor ROWS UNBOUNDED PRECEDING)
        / sum(n_late) OVER ()                                           AS cumulative_share_of_late,
    row_number() OVER (ORDER BY n_late DESC, vendor) / count(*) OVER () AS share_of_vendors
FROM v
ORDER BY n_late DESC;
