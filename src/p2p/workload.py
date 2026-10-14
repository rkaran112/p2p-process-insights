"""How much manual work is coming next month?

A weekly forecast of manual steps, backtested with rolling origins, so a
team lead can staff against a number with a known error rather than a guess.

The log only contains PO lines *created in 2018*.  Early 2018 therefore
under-counts work (invoices for 2017 orders are missing) and 2019 collapses
(no new orders enter).  Neither is real demand, so the forecast is fitted and
scored only on the steady-state window between them, which is chosen from
the data, not by hand.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from . import config as C


def steady_window(w: pd.DataFrame, frac: float = 0.8) -> pd.DataFrame:
    """Weeks whose PO-creation volume is within the normal range for 2018.

    PO creation is the inflow that the sampling frame cuts off, so it marks
    where the frame distorts the workload: the first and last weeks where it
    falls below `frac` of the 2018 median bound the window.
    """
    w = w.copy()
    w["week"] = pd.to_datetime(w["week"])
    yr = w[(w["week"] >= "2018-01-01") & (w["week"] < "2019-01-01")]
    med = yr["po_lines_created"].median()
    ok = yr[yr["manual_events"] >= frac * yr["manual_events"].median()]
    ok = ok[ok["po_lines_created"] >= 0.5 * med]
    start, end = ok["week"].min(), ok["week"].max()
    return w[(w["week"] >= start) & (w["week"] <= end)].reset_index(drop=True)


def is_month_end_week(weeks: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """True for the (Monday-start) week that contains a month's last day."""
    w = pd.DatetimeIndex(weeks)
    return np.asarray((w + pd.Timedelta(days=7)).month != w.month)


def week_of_month(weeks: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """1 for a week starting on the 1st-7th, 2 for the 8th-14th, ... 5 for 29th-31st."""
    return np.asarray((pd.DatetimeIndex(weeks).day - 1) // 7 + 1)


def _seasonal(history: np.ndarray, h: int, pos_hist: np.ndarray,
              pos_future: np.ndarray, min_obs: int = 3) -> np.ndarray:
    """4-week level with a multiplicative position effect learned from history."""
    mean = history.mean()
    mult = {p: history[pos_hist == p].mean() / mean
            for p in np.unique(pos_hist) if (pos_hist == p).sum() >= min_obs}
    f_hist = np.array([mult.get(p, 1.0) for p in pos_hist[-4:]])
    level = (history[-4:] / f_hist).mean()
    return level * np.array([mult.get(p, 1.0) for p in pos_future])


def _forecast(method: str, history: np.ndarray, h: int,
              me_hist: np.ndarray | None = None,
              me_future: np.ndarray | None = None,
              wom_hist: np.ndarray | None = None,
              wom_future: np.ndarray | None = None) -> np.ndarray:
    if method == "moving_average_month_end":
        # Hypothesis: month-end close lifts manual work.  The lift is learned
        # from history only and applied to forecast weeks holding a month end.
        return _seasonal(history, h, me_hist.astype(int), me_future.astype(int))
    if method == "moving_average_week_of_month":
        # Same idea, but every week-of-month position gets its own effect, so
        # the data decides *which* part of the month is busy.
        return _seasonal(history, h, wom_hist, wom_future)
    if method == "naive_last_week":
        return np.repeat(history[-1], h)
    if method == "moving_average_4w":
        return np.repeat(history[-4:].mean(), h)
    if method == "holt_damped":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = ExponentialSmoothing(history, trend="add", damped_trend=True,
                                       initialization_method="estimated").fit()
        return fit.forecast(h)
    raise ValueError(method)


METHODS = ("naive_last_week", "moving_average_4w", "holt_damped",
           "moving_average_month_end", "moving_average_week_of_month")


def backtest(series: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling-origin backtest: fit on everything before, forecast the next h weeks."""
    h = C.WORKLOAD.horizon_weeks
    k = C.WORKLOAD.n_backtest_folds
    y = series.to_numpy(dtype=float)
    me = is_month_end_week(series.index)
    wom = week_of_month(series.index)
    origins = [len(y) - h * (i + 1) for i in range(k)][::-1]
    rows, preds = [], []
    for o in origins:
        if o < 12:
            continue
        actual = y[o:o + h]
        for m in METHODS:
            f = _forecast(m, y[:o], h, me[:o], me[o:o + h], wom[:o], wom[o:o + h])
            ape = np.abs(f - actual) / actual
            rows.append({"origin_week": str(series.index[o].date()), "method": m,
                         "mape": float(ape.mean())})
            for j in range(h):
                preds.append({"week": series.index[o + j], "method": m,
                              "forecast": float(f[j]), "actual": float(actual[j])})
    return pd.DataFrame(rows), pd.DataFrame(preds)


def run(weekly: pd.DataFrame) -> dict:
    win = steady_window(weekly)
    s = win.set_index("week")["manual_events"]
    folds, preds = backtest(s)
    score = folds.groupby("method")["mape"].mean().sort_values()
    best = score.index[0]
    per_person = float((win["manual_events"] / win["active_people"]).median())
    mape = float(score[best])
    me = is_month_end_week(win["week"])
    month_end_lift = float(win.loc[me, "manual_events"].mean()
                           / win.loc[~me, "manual_events"].mean())
    wom = week_of_month(win["week"])
    by_pos = win.groupby(wom)["manual_events"].mean()
    wom_effect = {int(k): float(v / win["manual_events"].mean()) for k, v in by_pos.items()}
    busiest = max(wom_effect, key=wom_effect.get)
    return {
        "summary": {
            "window_start": str(win["week"].min().date()),
            "window_end": str(win["week"].max().date()),
            "n_weeks": int(len(win)),
            "median_manual_events_per_week": float(win["manual_events"].median()),
            "median_active_people_per_week": float(win["active_people"].median()),
            "manual_events_per_person_week": per_person,
            "horizon_weeks": C.WORKLOAD.horizon_weeks,
            "n_folds": int(folds["origin_week"].nunique()),
            "mape_by_method": {k: float(v) for k, v in score.items()},
            "best_method": best,
            "best_mape": mape,
            "month_end_lift": month_end_lift,
            "n_month_end_weeks": int(me.sum()),
            "week_of_month_effect": wom_effect,
            "busiest_week_of_month": busiest,
            "busiest_week_lift": wom_effect[busiest] / np.mean(
                [v for k, v in wom_effect.items() if k != busiest]),
            # Error expressed in people: the forecast miss divided by what one
            # person handles in a typical week.
            "typical_miss_people": float(mape * win["manual_events"].median() / per_person),
        },
        "window": win,
        "backtest_predictions": preds,
        "folds": folds,
    }
