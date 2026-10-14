"""Central configuration.

Every assumption a reviewer might challenge lives here, so it can be found,
cited and changed in one place.  Nothing downstream hard-codes a constant that
is defined in this file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------
SEED = 20261014

# --------------------------------------------------------------------------
# Paths.  Raw data can be redirected with P2P_DATA_DIR so a 700 MB XES file
# does not have to live inside a synced folder.
# --------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("P2P_DATA_DIR", REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
SQL_DIR = REPO_ROOT / "sql"
OUT_DIR = REPO_ROOT / "outputs"
FIG_DIR = OUT_DIR / "figures"
TAB_DIR = OUT_DIR / "tables"
DOC_DIR = REPO_ROOT / "docs"
SLIDE_DIR = REPO_ROOT / "slides"
DASH_DIR = REPO_ROOT / "dashboard"

for _d in (RAW_DIR, INTERIM_DIR, OUT_DIR, FIG_DIR, TAB_DIR, DOC_DIR, SLIDE_DIR,
           DASH_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Data source: BPI Challenge 2019, 4TU.ResearchData.
# --------------------------------------------------------------------------
XES_URL = ("https://data.4tu.nl/file/35ed7122-966a-484e-a0e1-749b64e3366d/"
           "864493d1-3a58-47f6-ad6f-27f95f995828")
XES_FILENAME = "BPI_Challenge_2019.xes"
XES_MD5 = "4eb909242351193a61e1c15b9c3cc814"
DATASET_DOI = "https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"

EVENTS_PARQUET = INTERIM_DIR / "events.parquet"
CASES_PARQUET = INTERIM_DIR / "cases.parquet"


@dataclass(frozen=True)
class PolicyAssumptions:
    """The business rules the log is judged against.

    The log carries no payment terms, so "late" cannot be read off the data.
    The threshold below is the default ceiling for business-to-business payment
    in the EU Late Payment Directive (2011/7/EU, art. 3(5)): 60 calendar days
    unless expressly agreed otherwise.  The sensitivity grid shows how much the
    findings depend on that choice.
    """

    late_days: int = 60
    late_days_sensitivity: tuple[int, ...] = (30, 45, 60, 90)

    # Directive 2011/7/EU: statutory interest is the ECB reference rate plus
    # at least 8 percentage points; the ECB main refinancing rate was 0.00%
    # throughout 2018.  Plus a fixed EUR 40 per late invoice for recovery
    # costs (art. 6).  These price an *exposure* - what vendors could claim -
    # not money the company is known to have paid.
    statutory_interest_rate: float = 0.08
    fixed_compensation_eur: float = 40.0

    # Events recorded by these users are system batch jobs, not people.
    batch_user_prefix: str = "batch_"
    # Events with no user at all (e.g. "Vendor creates invoice") are logged
    # on behalf of the vendor, not by the company's staff.
    vendor_user: str = "NONE"

    # Timestamps outside this window are data-entry errors, not 1948 invoices.
    valid_ts_start: str = "2017-01-01"
    valid_ts_end: str = "2020-01-01"


@dataclass(frozen=True)
class ModelAssumptions:
    """Late-payment classifier settings."""

    # Out-of-time split on the date the invoice was recorded.
    test_from: str = "2018-09-01"
    # Review-capacity framing: the AP team works the riskiest X% of invoices.
    review_share: float = 0.10
    min_vendor_history: int = 20
    gbm_params: tuple[tuple[str, object], ...] = (
        ("learning_rate", 0.05),
        ("max_iter", 300),
        ("max_leaf_nodes", 31),
        ("min_samples_leaf", 200),
        ("l2_regularization", 1.0),
    )


@dataclass(frozen=True)
class WorkloadAssumptions:
    """Weekly manual-workload forecast settings."""

    horizon_weeks: int = 4
    n_backtest_folds: int = 6


POLICY = PolicyAssumptions()
MODEL = ModelAssumptions()
WORKLOAD = WorkloadAssumptions()

# Activities that undo or redo earlier work.  Each one is a touch the process
# would not need if the first pass had been right.
REWORK_ACTIVITIES = (
    "Change Price",
    "Change Quantity",
    "Change Approval for Purchase Order",
    "Change Delivery Indicator",
    "Change Storage Location",
    "Change Currency",
    "Change Final Invoice Indicator",
    "Change payment term",
    "Change Rejection Indicator",
    "Cancel Invoice Receipt",
    "Cancel Goods Receipt",
    "Cancel Subsequent Invoice",
    "Delete Purchase Order Item",
    "Reactivate Purchase Order Item",
    "Block Purchase Order Item",
    "Vendor creates debit memo",
)
