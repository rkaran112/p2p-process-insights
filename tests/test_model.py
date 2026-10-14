"""The classifier's evaluation helpers and its leakage guard."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from p2p import model as M  # noqa: E402
from p2p import workload as W  # noqa: E402


def test_capture_at_perfect_and_random_scores():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    perfect = M.capture_at(y, y.astype(float), 0.2)
    assert perfect["capture"] == 1.0 and perfect["precision"] == 1.0
    assert perfect["lift"] == pytest.approx(5.0)
    worst = M.capture_at(y, -y.astype(float), 0.2)
    assert worst["capture"] == 0.0


def test_vendor_history_learned_from_train_only():
    train = pd.DataFrame({"vendor": ["a"] * 30 + ["b"] * 30,
                          "late": [1] * 30 + [0] * 30})
    test = pd.DataFrame({"vendor": ["a", "b", "new"]})
    out = M._vendor_history(train, test)
    prior = 0.5
    # A vendor never seen in training gets the prior, not its test outcome.
    assert out.loc[2, "vendor_late_rate"] == pytest.approx(prior)
    assert out.loc[2, "log_vendor_invoices"] == 0
    # Seen vendors are shrunk toward the prior, not reported raw.
    assert prior < out.loc[0, "vendor_late_rate"] < 1.0
    assert 0.0 < out.loc[1, "vendor_late_rate"] < prior


def test_steady_window_cuts_the_sampling_frame_edges():
    weeks = pd.date_range("2017-12-25", periods=70, freq="W-MON")
    manual = np.full(70, 1000)
    manual[:3] = 200          # ramp-up: 2017 orders missing
    manual[-12:] = 150        # 2019: no new orders enter
    po = np.where(manual > 500, 400, 20)
    w = pd.DataFrame({"week": weeks, "manual_events": manual,
                      "po_lines_created": po, "active_people": 50})
    win = W.steady_window(w)
    assert win["manual_events"].min() == 1000
    assert win["week"].max() < pd.Timestamp("2019-01-01")


def test_backtest_scores_a_flat_series_perfectly_with_the_naive_method():
    s = pd.Series(np.full(40, 500.0),
                  index=pd.date_range("2018-01-01", periods=40, freq="W-MON"))
    folds, _ = W.backtest(s)
    naive = folds[folds["method"] == "naive_last_week"]["mape"]
    assert np.allclose(naive, 0.0)


def test_month_end_week_flags_the_week_holding_the_last_day():
    weeks = pd.to_datetime(["2018-03-26", "2018-04-02", "2018-04-23", "2018-04-30",
                            "2018-09-24"])
    # 31 Mar (Sat) is in the week of 26 Mar; 30 Apr is a Monday; 30 Sep a Sunday.
    assert W.is_month_end_week(weeks).tolist() == [True, False, False, True, True]


def test_month_end_method_recovers_a_pure_month_end_pattern():
    idx = pd.date_range("2018-01-01", periods=40, freq="W-MON")
    me = W.is_month_end_week(idx)
    s = pd.Series(np.where(me, 1500.0, 1000.0), index=idx)
    folds, _ = W.backtest(s)
    assert np.allclose(folds[folds["method"] == "moving_average_month_end"]["mape"], 0.0)
    assert folds[folds["method"] == "moving_average_4w"]["mape"].mean() > 0.05


def test_week_of_month_positions():
    days = pd.to_datetime(["2018-04-02", "2018-04-09", "2018-04-16", "2018-04-23",
                           "2018-04-30"])
    assert W.week_of_month(days).tolist() == [1, 2, 3, 4, 5]


def test_week_of_month_method_recovers_a_fourth_week_spike():
    idx = pd.date_range("2018-01-01", periods=48, freq="W-MON")
    s = pd.Series(np.where(W.week_of_month(idx) == 4, 1400.0, 1000.0), index=idx)
    folds, _ = W.backtest(s)
    wom = folds[folds["method"] == "moving_average_week_of_month"]["mape"]
    flat = folds[folds["method"] == "moving_average_4w"]["mape"]
    # Not exactly zero: a position seen fewer than 3 times (the occasional 5th
    # week) deliberately falls back to no adjustment.
    assert wom.mean() < 0.03
    assert wom.mean() < flat.mean() / 3
