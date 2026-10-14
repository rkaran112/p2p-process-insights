"""The SQL layer, run on hand-built event logs whose right answers are known.

Each case below is constructed to trip exactly one rule (or, as a control,
none), so a failure points at the rule that broke.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2p import process as PR  # noqa: E402

LOG_END = "2018-12-31T00:00:00.000Z"


def ts(day: str) -> str:
    return f"{day}T10:00:00.000Z"


CASES = [
    # case_id, item_category, vendor
    ("clean", "3-way match, invoice after GR", "v1"),
    ("po_late", "3-way match, invoice before GR", "v1"),
    ("paid_early", "3-way match, invoice before GR", "v2"),
    ("slow", "2-way match", "v2"),
    ("open_long", "2-way match", "v3"),
    ("recent_fast", "2-way match", "v3"),
    ("recent_open", "2-way match", "v3"),
    ("bad_pair", "2-way match", "v3"),
    ("multi", "2-way match", "v4"),
    ("consign", "Consignment", "v4"),
    ("end", "2-way match", "v4"),
]

EVENTS = {
    # Control: the textbook 3-way flow, paid in 20 days.
    "clean": [("Create Purchase Order Item", "2018-01-01", "user_1"),
              ("Vendor creates invoice", "2018-01-05", "NONE"),
              ("Record Goods Receipt", "2018-01-06", "user_1"),
              ("Record Invoice Receipt", "2018-01-07", "user_2"),
              ("Clear Invoice", "2018-01-27", "batch_01")],
    # Vendor invoiced before the PO existed.
    "po_late": [("Vendor creates invoice", "2018-02-01", "NONE"),
                ("Create Purchase Order Item", "2018-02-10", "user_1"),
                ("Record Goods Receipt", "2018-02-11", "user_1"),
                ("Record Invoice Receipt", "2018-02-12", "user_2"),
                ("Clear Invoice", "2018-02-20", "batch_01")],
    # Paid before the goods arrived, on a 3-way line.
    "paid_early": [("Create Purchase Order Item", "2018-03-01", "user_1"),
                   ("Record Invoice Receipt", "2018-03-02", "user_2"),
                   ("Clear Invoice", "2018-03-05", "batch_01"),
                   ("Record Goods Receipt", "2018-03-10", "user_1")],
    # Paid in 90 days: late at 60.
    "slow": [("Create Purchase Order Item", "2018-04-01", "user_1"),
             ("Record Invoice Receipt", "2018-04-02", "user_2"),
             ("Change Price", "2018-04-03", "user_1"),
             ("Clear Invoice", "2018-07-01", "batch_01")],
    # Never paid, open 200+ days at log end: late.
    "open_long": [("Create Purchase Order Item", "2018-05-01", "user_1"),
                  ("Record Invoice Receipt", "2018-05-02", "user_2")],
    # Recorded 20 days before log end and paid fast: still censored, because
    # an unpaid twin recorded the same day would also have been censored.
    "recent_fast": [("Create Purchase Order Item", "2018-12-01", "user_1"),
                    ("Record Invoice Receipt", "2018-12-10", "user_2"),
                    ("Clear Invoice", "2018-12-12", "batch_01")],
    "recent_open": [("Create Purchase Order Item", "2018-12-01", "user_1"),
                    ("Record Invoice Receipt", "2018-12-10", "user_2")],
    # Clearance dated before the invoice: the pairing cannot be trusted.
    "bad_pair": [("Create Purchase Order Item", "2018-06-01", "user_1"),
                 ("Clear Invoice", "2018-06-02", "batch_01"),
                 ("Record Invoice Receipt", "2018-06-10", "user_2")],
    # Four invoices on one line worth 400: each carries 100, not 400.
    "multi": [("Create Purchase Order Item", "2018-07-01", "user_1")]
             + [("Record Invoice Receipt", f"2018-07-0{d}", "user_2") for d in (2, 3, 4, 5)]
             + [("Clear Invoice", "2018-07-20", "batch_01")],
    # Consignment lines should carry no PO-level invoice.
    "consign": [("Create Purchase Order Item", "2018-08-01", "user_1"),
                ("Record Goods Receipt", "2018-08-02", "user_1"),
                ("Record Invoice Receipt", "2018-08-03", "user_2"),
                ("Clear Invoice", "2018-08-10", "batch_01")],
    # Fixes the log end, and carries one impossible date that must be ignored.
    "end": [("Create Purchase Order Item", "2018-01-01", "user_1"),
            ("Record Goods Receipt", "1948-01-26", "user_1"),
            ("Change Quantity", LOG_END[:10], "user_1")],
}


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    d = tmp_path_factory.mktemp("log")
    ev = []
    for cid, steps in EVENTS.items():
        value = 400.0 if cid == "multi" else 1000.0
        for i, (act, day, user) in enumerate(steps):
            ev.append({"case_id": cid, "event_idx": i, "activity": act,
                       "ts_raw": ts(day), "user": user, "net_worth_eur": value})
    ev_df = pd.DataFrame(ev)
    ev_df["event_idx"] = ev_df["event_idx"].astype("int32")
    cases = pd.DataFrame([
        {"case_id": c, "item_category": cat, "vendor": v, "po_document": c,
         "po_item": "00001", "item_type": "Standard", "gr_based_iv": cat.endswith("after GR"),
         "goods_receipt_required": cat.startswith("3-way"), "source_system": "s",
         "doc_category": "Purchase order", "company": "c0", "spend_class": "NPR",
         "spend_area": "Logistics", "sub_spend_area": "x", "vendor_name": v,
         "document_type": "Standard PO"}
        for c, cat, v in CASES])
    ep, cp = d / "events.parquet", d / "cases.parquet"
    ev_df.to_parquet(ep, index=False)
    cases.to_parquet(cp, index=False)
    return PR.build(events_parquet=ep, cases_parquet=cp)


@pytest.fixture(scope="module")
def cf(con):
    return PR.table(con, "case_facts").set_index("case_id")


@pytest.fixture(scope="module")
def inv(con):
    return PR.table(con, "invoices").set_index("case_id")


def test_po_after_invoice_flags_only_the_retroactive_po(cf):
    assert cf.loc["po_late", "po_after_invoice"]
    assert not cf.loc[cf.index != "po_late", "po_after_invoice"].any()


def test_paid_before_goods_receipt_only_on_three_way_lines(cf):
    assert cf.loc["paid_early", "paid_before_goods_receipt"]
    # "slow" is paid with no goods receipt at all, but it is 2-way: no breach.
    assert not cf.loc["slow", "paid_before_goods_receipt"]
    assert not cf.loc["clean", "paid_before_goods_receipt"]


def test_invoice_on_consignment(cf):
    assert cf.loc["consign", "invoice_on_consignment"]
    assert not cf.loc["clean", "any_violation"]


def test_impossible_dates_are_ignored(con, cf):
    n_bad = con.execute("SELECT count(*) FROM events WHERE NOT ts_valid").fetchone()[0]
    assert n_bad == 1
    assert pd.isna(cf.loc["end", "first_goods_receipt"])


def test_late_outcomes(inv):
    assert inv.loc["clean", "is_late"] == False   # noqa: E712  20 days
    assert inv.loc["slow", "is_late"] == True     # noqa: E712  90 days
    assert inv.loc["open_long", "is_late"] == True  # noqa: E712  never paid, 240 days open


def test_censoring_does_not_keep_only_the_fast_payers(inv):
    # Both recorded 21 days before the log ends.  Keeping the paid one and
    # dropping the unpaid one would bias the late rate down.
    assert pd.isna(inv.loc["recent_fast", "is_late"])
    assert pd.isna(inv.loc["recent_open", "is_late"])


def test_failed_pairing_is_unknown_not_on_time(inv):
    assert not inv.loc["bad_pair", "pairing_ok"]
    assert pd.isna(inv.loc["bad_pair", "is_late"])


def test_one_row_per_line_and_value_spread(inv):
    assert inv.index.is_unique
    assert inv.loc["multi", "n_invoices_on_line"] == 4
    assert inv.loc["multi", "invoice_value_eur"] == pytest.approx(100.0)


def test_rework_before_invoice_uses_only_the_past(inv):
    # "slow" has its price change a day AFTER the invoice: not visible at scoring.
    assert inv.loc["slow", "n_rework_before"] == 0


def test_python_late_flag_matches_sql(inv):
    f = PR.late_flag(inv.reset_index(), 60)
    sql = inv["is_late"].astype("float").to_numpy()
    assert np.array_equal(np.isnan(f.to_numpy()), np.isnan(sql))
    m = ~np.isnan(sql)
    assert np.array_equal(f.to_numpy()[m], sql[m])


def test_variants_collapse_repeats(con):
    v = PR.table(con, "variants")
    assert not v["variant"].str.contains(
        "Record Invoice Receipt > Record Invoice Receipt").any(), \
        "four consecutive invoices should collapse to one step"
    # "multi" and "recent_fast" now share one variant.
    row = v[v["variant"] == "Create Purchase Order Item > Record Invoice Receipt > Clear Invoice"]
    assert row["n_cases"].item() == 2
    assert v["n_cases"].sum() == len(EVENTS)
