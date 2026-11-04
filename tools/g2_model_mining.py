"""Exhaustive model-mining lab for the G2 sample rows.

The question this answers is not "which model wins" but "what can any learner
extract from 25 labelled rows".  Every model is scored leave-one-out against the
same target, so a model that memorises is visibly indistinguishable from one that
generalises: an in-sample 25/25 next to a leave-one-out 2/25 is memorisation.

Targets
    D        = balance - floor - gold          (the drop the reference applied)
    D/full   = D / sum of monthly budgets      (scale-free, heavy tail trimmed)
    gold     = the published amount            (what the gate compares)

Features include categorical columns (currency, request type, partial-payment
permission, payment methods, which protected categories exist) so tree models can
split on them natively; CatBoost and HistGradientBoosting get the raw categories,
XGBoost and the linear family get one-hot columns.

Nothing here is imported by production code.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

import code.forecast as forecast
from code.recurrence import detect_recurrence

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock_dataset"

_RESOLVED_DIGITS = {
    "253": "4365000",
    "1442": "100000",
    "1545": "41272.00",
    "1700": "2854.00",
    "1786": "704.05",
}
IMAGE_AMOUNTS = {"event_" + digits: amount for digits, amount in _RESOLVED_DIGITS.items()}

PROJECTED = frozenset(
    forecast.FORECASTABLE_EXPENSE_CATEGORIES - forecast.DISCRETIONARY_CATEGORIES
)


def dec(value) -> Decimal:
    return Decimal(str(value).strip() or "0")


def load():
    events = pd.read_csv(MOCK / "financial_events.csv", dtype=str, keep_default_na=False).to_dict("records")
    for event in events:
        key = str(event.get("event_id", "")).strip()
        if key in IMAGE_AMOUNTS and not str(event.get("amount", "")).strip():
            event["amount"] = IMAGE_AMOUNTS[key]
    profiles = pd.read_csv(MOCK / "financial_profiles.csv", dtype=str, keep_default_na=False).set_index("user_id")
    fx = pd.read_csv(MOCK / "exchange_rates.csv", dtype=str, keep_default_na=False).to_dict("records")
    sample = pd.read_csv(MOCK / "sample_requests.csv", dtype=str, keep_default_na=False)
    flags = detect_recurrence(events)
    return events, profiles, fx, sample, flags


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / Decimal(2)


def budgets_for(user_events, req_date, home_currency, fx):
    """Median monthly home-currency total per forecastable category."""
    monthly = {}
    for category, day, amount in forecast._eligible_expense_rows(
        user_events,
        categories=PROJECTED,
        home_currency=home_currency,
        fx_rows=fx,
        warnings=[],
        warn_on_fx=True,
    ):
        if day >= req_date:
            continue
        bucket = monthly.setdefault(category, {})
        key = (day.year, day.month)
        bucket[key] = bucket.get(key, Decimal(0)) + amount
    out = {}
    for category, months in monthly.items():
        if len(months) < forecast.MIN_BUDGET_MONTHS:
            continue
        out[category] = _median(list(months.values()))
    return out


def build_frame(source=None):
    """Feature frame for a request file.

    ``source`` is a path or an already-loaded DataFrame; the default is the 25
    labelled sample.  A file without gold columns (the 250 real requests) gets
    ``gold`` as NaN and ``informative`` False.
    """
    events, profiles, fx, sample, flags = load()
    if source is not None:
        sample = source if isinstance(source, pd.DataFrame) else pd.read_csv(
            source, dtype=str, keep_default_na=False
        )
    records = []
    for _, req in sample.iterrows():
        user_id = req["user_id"]
        profile = profiles.loc[user_id].to_dict()
        user_events = [e for e in events if e["user_id"] == user_id]
        req_date = date.fromisoformat(req["request_date"][:10])
        home = profile["home_currency"]
        budgets = budgets_for(user_events, req_date, home, fx)
        mtd = {}
        for category, day, amount in forecast._eligible_expense_rows(
            user_events,
            categories=PROJECTED,
            home_currency=home,
            fx_rows=fx,
            warnings=[],
            warn_on_fx=True,
        ):
            if req_date.replace(day=1) <= day < req_date:
                mtd[category] = mtd.get(category, Decimal(0)) + amount

        balance = dec(profile["current_available_balance"])
        floor = dec(profile["minimum_balance_to_keep"])
        requested = dec(req["requested_amount"])
        gold_text = str(req.get("amount_safe_to_pay", "") or "").strip()
        gold = dec(gold_text) if gold_text else Decimal("0")
        full = sum(budgets.values(), Decimal(0))
        mtd_total = sum(mtd.values(), Decimal(0))
        days_left = (date(req_date.year + (req_date.month == 12), req_date.month % 12 + 1, 1) - req_date).days
        days_in_month = (date(req_date.year, req_date.month, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        days_in_month = days_in_month.day
        deadline = str(req.get("desired_completion_date") or "").strip()
        horizon = (date.fromisoformat(deadline[:10]) - req_date).days if deadline else 90
        salary_rows = [
            e for e in user_events
            if str(e.get("event_type")).lower() == "income" and str(e.get("category")).lower() == "salary"
        ]
        salary_amounts = [dec(e["amount"]) for e in salary_rows if str(e.get("amount", "")).strip()]
        salary = float(_median(salary_amounts)) if salary_amounts else 0.0

        record = {
            "request_id": req["request_id"],
            "gold": float(gold),
            "balance": float(balance),
            "floor": float(floor),
            "requested": float(requested),
            "full": float(full),
            "mtd": float(mtd_total),
            "days_left": days_left,
            "days_in_month": days_in_month,
            "to_deadline": horizon,
            "dom": req_date.day,
            "ncat": len(budgets),
            "salary_amount": salary,
            "n_protect": len([c for c in str(profile.get("expense_categories_to_protect", "")).split("|") if c]),
            "max_installments": float(str(profile.get("max_installment_months") or "0").strip() or 0),
            "requested_ratio": float(requested) / max(float(balance), 1.0),
            "full_ratio": float(full) / max(float(balance), 1.0),
            "mtd_ratio": float(mtd_total) / max(float(full), 1.0),
            "income_cover": salary / max(float(full), 1.0),
            "headroom": float(balance - floor),
            # categorical columns, kept raw
            "currency": profile["home_currency"],
            "request_type": req.get("request_type", ""),
            "allows_partial": str(req.get("allows_partial_payment", "")).lower(),
            "payment_methods": profile.get("payment_methods_user_will_consider", ""),
            "has_rent": "yes" if "rent" in budgets else "no",
            "has_housing": "yes" if "housing" in budgets else "no",
            "has_education": "yes" if "education" in budgets else "no",
        }
        record["D"] = record["balance"] - record["floor"] - record["gold"]
        record["D_over_full"] = record["D"] / max(record["full"], 1.0)
        records.append(record)
    frame = pd.DataFrame.from_records(records)
    frame["informative"] = (frame["gold"] > 0.01) & (frame["gold"] < frame["requested"] - 0.01)
    return frame


NUMERIC = [
    "balance", "floor", "requested", "full", "mtd", "days_left", "days_in_month",
    "to_deadline", "dom", "ncat", "salary_amount", "n_protect", "max_installments",
    "requested_ratio", "full_ratio", "mtd_ratio", "income_cover", "headroom",
]
CATEGORICAL = ["currency", "request_type", "allows_partial", "payment_methods",
               "has_rent", "has_housing", "has_education"]


def matrices(frame, target):
    from sklearn.compose import ColumnTransformer
    from sklearn.preprocessing import OneHotEncoder

    X = frame[NUMERIC + CATEGORICAL]
    y = frame[target].to_numpy(float)
    encoder = ColumnTransformer(
        [("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL)],
        remainder="passthrough",
    )
    dense = encoder.fit_transform(X)
    names = list(encoder.get_feature_names_out())
    return dense, y, names, X


def model_zoo():
    from sklearn.ensemble import (
        ExtraTreesRegressor,
        GradientBoostingRegressor,
        HistGradientBoostingRegressor,
        RandomForestRegressor,
    )
    from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, Ridge
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.neural_network import MLPRegressor
    from sklearn.svm import SVR
    from sklearn.tree import DecisionTreeRegressor

    zoo = [
        ("OLS", LinearRegression()),
        ("Ridge a=1", Ridge(alpha=1.0)),
        ("Lasso a=.01", Lasso(alpha=0.01, max_iter=100000)),
        ("ElasticNet", ElasticNet(alpha=0.01, l1_ratio=0.5, max_iter=100000)),
        ("KNN k=3", KNeighborsRegressor(n_neighbors=3)),
        ("SVR rbf", SVR(C=10.0, epsilon=0.05)),
        ("MLP", MLPRegressor(hidden_layer_sizes=(16,), max_iter=4000, random_state=0)),
        ("Tree d=2", DecisionTreeRegressor(max_depth=2, min_samples_leaf=3, random_state=0)),
        ("Tree d=3", DecisionTreeRegressor(max_depth=3, min_samples_leaf=3, random_state=0)),
        ("Tree d=4", DecisionTreeRegressor(max_depth=4, min_samples_leaf=2, random_state=0)),
        ("RF 400", RandomForestRegressor(n_estimators=400, random_state=0)),
        ("ExtraTrees 400", ExtraTreesRegressor(n_estimators=400, random_state=0)),
        ("GBM", GradientBoostingRegressor(random_state=0)),
        ("HistGBM", HistGradientBoostingRegressor(max_depth=3, random_state=0)),
    ]
    try:
        from xgboost import XGBRegressor

        zoo.append(("XGBoost d=3", XGBRegressor(
            n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.9,
            random_state=0, verbosity=0, n_jobs=1,
        )))
    except Exception as exc:  # pragma: no cover - optional dependency
        print(f"  (xgboost unavailable: {type(exc).__name__})")
    try:
        from catboost import CatBoostRegressor

        zoo.append(("CatBoost d=3", CatBoostRegressor(
            iterations=300, depth=3, learning_rate=0.05, random_seed=0,
            verbose=0, thread_count=1,
        )))
    except Exception as exc:  # pragma: no cover - optional dependency
        print(f"  (catboost unavailable: {type(exc).__name__})")
    return zoo


def loo_scores(model, X, y):
    from sklearn.model_selection import LeaveOneOut, cross_val_predict

    predictions = cross_val_predict(model, X, y, cv=LeaveOneOut())
    residual = predictions - y
    ss_res = float((residual ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot else float("nan")
    exact = int(np.sum(np.abs(residual) <= 0.01))
    scale = np.maximum(np.abs(y), 1e-9)
    return {
        "r2": r2,
        "mae": float(np.median(np.abs(residual))),
        "mre": float(np.median(np.abs(residual) / scale)),
        "exact": exact,
        "n": int(len(y)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", default="D_over_full,D,gold")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    frame = build_frame()
    informative = frame[frame["informative"]].reset_index(drop=True)
    print(f"labelled rows {len(frame)}, informative {len(informative)}")
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]

    report = {}
    for target in targets:
        dense, y, names, raw = matrices(informative, target)
        print(f"\n=== target {target} (n={len(y)}) ===")
        print(f"{'model':<18}{'LOO R2':>10}{'medAE':>12}{'medRE':>9}{'exact':>7}")
        rows = {}
        for label, model in model_zoo():
            try:
                scores = loo_scores(model, dense, y)
            except Exception as exc:
                print(f"{label:<18} failed: {type(exc).__name__}: {exc}")
                continue
            rows[label] = scores
            print(f"{label:<18}{scores['r2']:>+10.3f}{scores['mae']:>12.4f}{scores['mre']:>9.3f}{scores['exact']:>4}/{scores['n']}")
        report[target] = rows

    # rules from the best tree, on the scale-free target
    if "D_over_full" in targets:
        from sklearn.tree import DecisionTreeRegressor, export_text

        dense, y, names, _ = matrices(informative, "D_over_full")
        tree = DecisionTreeRegressor(max_depth=4, min_samples_leaf=2, random_state=0).fit(dense, y)
        print("\n=== tree rules for D_over_full (depth 4) ===")
        print(export_text(tree, feature_names=names, decimals=3))
        in_sample = int(np.sum(np.abs(tree.predict(dense) - y) <= 0.01))
        print(f"in-sample exact {in_sample}/{len(y)} (memorisation ceiling)")

    if args.json:
        path = ROOT / args.json
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("\nwrote", path)
    return report


if __name__ == "__main__":
    main()
