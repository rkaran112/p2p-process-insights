-- One row per purchase-order line: milestone dates, effort, rework and the
-- compliance checks.  "First" means the earliest valid occurrence.
CREATE OR REPLACE TABLE case_facts AS
WITH ev AS (
    SELECT * FROM events WHERE ts_valid
),
milestones AS (
    SELECT
        case_id,
        min(ts) FILTER (WHERE activity = 'Create Purchase Order Item')   AS po_created,
        min(ts) FILTER (WHERE activity = 'Vendor creates invoice')       AS first_vendor_invoice,
        min(ts) FILTER (WHERE activity = 'Record Goods Receipt')         AS first_goods_receipt,
        min(ts) FILTER (WHERE activity = 'Record Invoice Receipt')       AS first_invoice_receipt,
        min(ts) FILTER (WHERE activity = 'Clear Invoice')                AS first_clear_invoice,
        min(ts) FILTER (WHERE activity = 'Remove Payment Block')         AS first_block_removed,
        max(ts)                                                          AS last_event,
        min(ts)                                                          AS first_event,
        count(*)                                                         AS n_events,
        count(*) FILTER (WHERE actor_type = 'human')                     AS n_manual,
        count(*) FILTER (WHERE actor_type = 'batch')                     AS n_batch,
        count(*) FILTER (WHERE activity IN ({rework_list}))              AS n_rework,
        count(*) FILTER (WHERE activity = 'Record Invoice Receipt')      AS n_invoices,
        count(*) FILTER (WHERE activity = 'Clear Invoice')               AS n_clears,
        count(*) FILTER (WHERE activity = 'Record Goods Receipt')        AS n_goods_receipts,
        count(*) FILTER (WHERE activity = 'Change Price')                AS n_price_changes,
        count(*) FILTER (WHERE activity = 'Remove Payment Block')        AS n_payment_block_removals,
        -- Value of the line when it was ordered.
        arg_min(net_worth_eur, ts) FILTER (WHERE activity = 'Create Purchase Order Item')
                                                                         AS po_value_eur
    FROM ev
    GROUP BY case_id
)
SELECT
    c.*,
    m.* EXCLUDE (case_id),
    c.item_category LIKE '3-way%'                                         AS needs_goods_receipt,

    -- Policy 1, "no PO, no pay": the vendor invoiced before the order existed,
    -- so the PO was raised after the fact to cover a purchase already made.
    coalesce(m.first_vendor_invoice < m.po_created, FALSE)                AS po_after_invoice,

    -- Policy 2, 3-way match: an invoice should not be paid before the goods
    -- it bills for have been received.  Also true when paid and never received.
    c.item_category LIKE '3-way%'
        AND m.first_clear_invoice IS NOT NULL
        AND (m.first_goods_receipt IS NULL
             OR m.first_clear_invoice < m.first_goods_receipt)            AS paid_before_goods_receipt,

    -- Policy 3: in the "invoice after GR" flow, the invoice should not even be
    -- booked before the goods receipt.
    c.item_category = '3-way match, invoice after GR'
        AND m.first_invoice_receipt IS NOT NULL
        AND (m.first_goods_receipt IS NULL
             OR m.first_invoice_receipt < m.first_goods_receipt)          AS invoice_before_gr_in_after_gr_flow,

    -- Consignment stock is invoiced outside the PO, so any PO-level invoice
    -- on a consignment line is out of process.
    c.item_category = 'Consignment' AND m.n_invoices > 0                  AS invoice_on_consignment,

    date_diff('hour', m.po_created, m.first_clear_invoice) / 24.0         AS po_to_first_payment_days
FROM cases c
JOIN milestones m USING (case_id);

CREATE OR REPLACE TABLE case_facts AS
SELECT
    *,
    po_after_invoice OR paid_before_goods_receipt
        OR invoice_before_gr_in_after_gr_flow OR invoice_on_consignment   AS any_violation
FROM case_facts;
