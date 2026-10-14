"""Which newly recorded invoices are going to be paid late?

Scored at the moment an invoice is recorded, using only what is known then.
The use case is a work queue: each week the accounts-payable team chases the
riskiest share of new invoices before they breach the threshold.  So the
headline metric is *capture* - of the invoices that did go late, how many were
in the reviewed share - not accuracy.

Split is out-of-time on the recording date.  Vendor history is computed on
the training period only, so the test period never leaks into a feature.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from . import config as C

CATEGORICAL = ["item_category", "document_type", "spend_area", "item_type",
               "company"]
NUMERIC = ["log_invoice_value", "log_po_value", "po_age_at_invoice_days",
           "n_rework_before", "vendor_late_rate", "log_vendor_invoices"]
BOOLEAN = ["gr_before_invoice", "recorded_by_batch", "gr_based_iv",
           "goods_receipt_required"]

# Plain-English names for the driver table in the memo.
LABELS = {
    "log_invoice_value": "invoice value (log)",
    "log_po_value": "PO line value (log)",
    "po_age_at_invoice_days": "days since the PO was raised",
    "n_rework_before": "changes/cancellations before the invoice",
    "vendor_late_rate": "vendor's past late rate",
    "log_vendor_invoices": "vendor's past invoice volume (log)",
    "gr_before_invoice": "goods already received",
    "recorded_by_batch": "invoice recorded automatically",
    "gr_based_iv": "GR-based invoice verification",
    "goods_receipt_required": "goods receipt required",
}


def _vendor_history(train: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Smoothed vendor late rate and volume, learned from `train` only."""
    m = C.MODEL.min_vendor_history
    prior = train["late"].mean()
    g = train.groupby("vendor")["late"].agg(["sum", "size"])
    rate = (g["sum"] + prior * m) / (g["size"] + m)      # shrink small vendors to the mean
    out = df.copy()
    out["vendor_late_rate"] = out["vendor"].map(rate).fillna(prior)
    out["log_vendor_invoices"] = np.log1p(out["vendor"].map(g["size"]).fillna(0))
    return out


def prepare(inv: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = inv[inv["is_late"].notna()].copy()
    d["late"] = d["is_late"].astype(int)
    d["log_invoice_value"] = np.log1p(d["invoice_value_eur"].clip(lower=0))
    d["log_po_value"] = np.log1p(d["po_value_eur"].fillna(0).clip(lower=0))
    d["po_age_at_invoice_days"] = d["po_age_at_invoice_days"].clip(-30, 400)
    for b in BOOLEAN:
        d[b] = d[b].fillna(False).astype(int)
    for c in CATEGORICAL:
        d[c] = d[c].fillna("missing").astype(str)
    cut = pd.Timestamp(C.MODEL.test_from)
    train = d[d["recorded_at"] < cut].copy()
    test = d[d["recorded_at"] >= cut].copy()
    return _vendor_history(train, train), _vendor_history(train, test)


def capture_at(y: np.ndarray, score: np.ndarray, share: float) -> dict:
    """If the team reviews the top `share` of invoices by score, what do they catch?"""
    n = max(1, int(round(len(y) * share)))
    top = np.argsort(-score, kind="stable")[:n]
    caught = y[top].sum()
    return {"review_share": share, "n_reviewed": int(n),
            "precision": float(caught / n),
            "capture": float(caught / max(y.sum(), 1)),
            # When most invoices go late, reviewing 10% cannot catch more
            # than 10% / base-rate of them, however good the model is.
            "max_possible_capture": float(min(1.0, n / max(y.sum(), 1))),
            "lift": float((caught / n) / max(y.mean(), 1e-12))}


def gains_curve(y: np.ndarray, score: np.ndarray, points: int = 50) -> pd.DataFrame:
    order = np.argsort(-score, kind="stable")
    cum = np.cumsum(y[order]) / max(y.sum(), 1)
    idx = np.linspace(0, len(y) - 1, points).astype(int)
    return pd.DataFrame({"share_reviewed": (idx + 1) / len(y), "share_captured": cum[idx]})


def _logit() -> Pipeline:
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=200), CATEGORICAL),
        ("num", StandardScaler(), NUMERIC + BOOLEAN),
    ])
    return Pipeline([("pre", pre),
                     ("clf", LogisticRegression(max_iter=2000, C=1.0))])


def _gbm() -> Pipeline:
    pre = ColumnTransformer([
        ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1,
                               encoded_missing_value=-1), CATEGORICAL),
        ("num", "passthrough", NUMERIC + BOOLEAN),
    ])
    clf = HistGradientBoostingClassifier(
        categorical_features=list(range(len(CATEGORICAL))),
        random_state=C.SEED, **dict(C.MODEL.gbm_params))
    return Pipeline([("pre", pre), ("clf", clf)])


def drivers(model: Pipeline) -> pd.DataFrame:
    """Logistic coefficients as odds ratios per one standard deviation."""
    pre = model.named_steps["pre"]
    names = pre.get_feature_names_out()
    coef = model.named_steps["clf"].coef_[0]
    d = pd.DataFrame({"feature": names, "coef": coef})
    d["odds_ratio"] = np.exp(d["coef"])
    d["feature"] = (d["feature"].str.replace("cat__", "", regex=False)
                                 .str.replace("num__", "", regex=False))
    d["label"] = d["feature"].map(LABELS).fillna(d["feature"])
    return d.reindex(d["coef"].abs().sort_values(ascending=False).index).reset_index(drop=True)


def run(inv: pd.DataFrame) -> dict:
    train, test = prepare(inv)
    cols = CATEGORICAL + NUMERIC + BOOLEAN
    Xtr, ytr = train[cols], train["late"].to_numpy()
    Xte, yte = test[cols], test["late"].to_numpy()
    share = C.MODEL.review_share

    results: dict = {
        "n_train": int(len(train)), "n_test": int(len(test)),
        "train_late_rate": float(ytr.mean()), "test_late_rate": float(yte.mean()),
        "test_from": C.MODEL.test_from, "models": {},
    }
    scores: dict[str, np.ndarray] = {
        # The rule an AP lead would use without a model: chase the vendors who
        # were late before.
        "vendor_history_rule": test["vendor_late_rate"].to_numpy(),
    }
    fitted = {}
    for name, mk in (("logistic", _logit), ("gradient_boosting", _gbm)):
        m = mk().fit(Xtr, ytr)
        fitted[name] = m
        scores[name] = m.predict_proba(Xte)[:, 1]
    rng = np.random.default_rng(C.SEED)
    scores["random"] = rng.random(len(yte))

    for name, s in scores.items():
        results["models"][name] = {
            "auc": float(roc_auc_score(yte, s)),
            "average_precision": float(average_precision_score(yte, s)),
            **capture_at(yte, s, share),
        }
    best = max(("logistic", "gradient_boosting"),
               key=lambda k: results["models"][k]["capture"])
    results["best_model"] = best

    drv = drivers(fitted["logistic"])
    perm = permutation_importance(fitted["gradient_boosting"], Xte, yte,
                                  scoring="roc_auc", n_repeats=3,
                                  random_state=C.SEED, n_jobs=1)
    imp = (pd.DataFrame({"feature": cols, "auc_drop": perm.importances_mean})
             .sort_values("auc_drop", ascending=False).reset_index(drop=True))
    imp["label"] = imp["feature"].map(LABELS).fillna(imp["feature"])

    gains = {k: gains_curve(yte, scores[k]) for k in
             ("gradient_boosting", "logistic", "vendor_history_rule", "random")}
    return {"summary": results, "drivers": drv, "importance": imp, "gains": gains}
