"""Transfer study: train on the 25 labelled rows, apply to the 250 real requests.

The 250 requests carry no gold, so the comparison is against the kernel's own
answer, which the investigation measured at corr +0.911 with gold.  Three
questions:

1. do the 25 labelled rows look like the 250 (feature shift)?
2. does a model fitted to the 25 behave sensibly on the 250, or does it fall
   apart outside its training points?
3. does a model fitted to the kernel's 250 answers reproduce gold on the 25?
   (If gold and the kernel shared a rule, this would succeed.)

Nothing here is imported by production code.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import code.forecast as forecast
from code.oracle import solve_safe_amount
from code.recurrence import detect_recurrence
from tools import g2_probe as lab
from tools.g2_model_mining import (
    CATEGORICAL,
    NUMERIC,
    build_frame,
    model_zoo,
)

MOCK = lab.MOCK


def kernel_answers(frame):
    """Kernel safe amount for every row, keyed by request_id."""
    events, profiles, fx, sample, options, messages, flags = lab.load_data()
    requests = pd.read_csv(MOCK / "requests.csv", dtype=str, keep_default_na=False)
    wanted = set(frame["request_id"])
    answers = {}
    for _, req in requests.iterrows():
        if req["request_id"] not in wanted:
            continue
        profile = profiles.loc[req["user_id"]].to_dict()
        user_events = [event for event in events if event["user_id"] == req["user_id"]]
        result = solve_safe_amount(
            req["user_id"],
            req["request_date"],
            req["requested_amount"],
            profile,
            user_events,
            fx,
            flags,
        )
        answers[req["request_id"]] = float(result["safe_amount"])
    return answers


def ratio_target(frame, label_column):
    """D/full from a label column (gold or kernel), so models share a target."""
    return (frame["balance"] - frame["floor"] - frame[label_column]) / frame["full"].clip(lower=1.0)


def encode(train_frame, other_frames):
    from sklearn.compose import ColumnTransformer
    from sklearn.preprocessing import OneHotEncoder

    encoder = ColumnTransformer(
        [("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL)],
        remainder="passthrough",
    )
    encoder.fit(train_frame[NUMERIC + CATEGORICAL])
    return [encoder.transform(f[NUMERIC + CATEGORICAL]) for f in other_frames]


def safe_from_ratio(frame, ratio):
    raw = frame["balance"].to_numpy(float) - frame["floor"].to_numpy(float) - ratio * frame["full"].to_numpy(float)
    return np.clip(raw, 0.0, frame["requested"].to_numpy(float))


def shift_table(small, big):
    rows = []
    for column in NUMERIC:
        a = small[column].to_numpy(float)
        b = big[column].to_numpy(float)
        sd = np.sqrt((a.var() + b.var()) / 2) or 1.0
        rows.append(
            {
                "feature": column,
                "n25_mean": a.mean(),
                "n250_mean": b.mean(),
                "std_diff": (a.mean() - b.mean()) / sd,
                "n25_cv": a.std() / (abs(a.mean()) or 1.0),
                "n250_cv": b.std() / (abs(b.mean()) or 1.0),
            }
        )
    return pd.DataFrame(rows)


def main():
    frame25 = build_frame()
    frame250 = build_frame(MOCK / "requests.csv")
    small = frame25[frame25["informative"]].reset_index(drop=True)
    print(f"labelled rows {len(frame25)} (informative {len(small)}), real requests {len(frame250)}")

    print("\n== 1. feature shift, 25 labelled vs 250 real ==")
    table = shift_table(frame25, frame250)
    print(table.round(3).to_string(index=False))
    print("\ncategorical shares (25 vs 250):")
    for column in CATEGORICAL:
        a = frame25[column].astype(str).value_counts(normalize=True)
        b = frame250[column].astype(str).value_counts(normalize=True)
        keys = sorted(set(a.index) | set(b.index))
        shown = ", ".join(f"{k}: {a.get(k,0):.2f}/{b.get(k,0):.2f}" for k in keys if a.get(k, 0) or b.get(k, 0))
        print(f"  {column:<18} {shown}")

    print("\n== 2. kernel answers for the 250 ==")
    answers = kernel_answers(frame250)
    frame250 = frame250.assign(kernel=[answers.get(r, float("nan")) for r in frame250["request_id"]])
    covered = frame250["kernel"].notna().sum()
    print(f"  kernel answered {covered}/{len(frame250)}")
    kernel_ratio = ratio_target(frame250, "kernel")

    print("\n== 3. fit on 25 gold, predict the 250, compare with the kernel ==")
    X25, X250 = encode(small, [small, frame250])
    y_ratio = ratio_target(small, "gold").to_numpy(float)
    kernel_safe = frame250["kernel"].to_numpy(float)
    print(f"{'model':<18}{'25 LOO medAE':>14}{'250 corr':>10}{'250 med|log|':>13}{'250 %zero':>11}{'250 %full':>11}")
    for label, model in model_zoo():
        try:
            model.fit(X25, y_ratio)
            predicted = safe_from_ratio(frame250, model.predict(X250))
        except Exception as exc:
            print(f"{label:<18} failed: {type(exc).__name__}")
            continue
        both = np.isfinite(kernel_safe) & np.isfinite(predicted)
        corr = float(pd.Series(predicted[both]).corr(pd.Series(kernel_safe[both]), method="spearman"))
        with np.errstate(divide="ignore", invalid="ignore"):
            logr = np.abs(np.log((predicted[both] + 1.0) / (kernel_safe[both] + 1.0)))
        zero_share = float((predicted <= 0.01).mean())
        full_share = float((predicted >= frame250["requested"].to_numpy(float) - 0.01).mean())
        # LOO on the 25 for reference
        from sklearn.model_selection import LeaveOneOut, cross_val_predict

        loo_pred = cross_val_predict(model, X25, y_ratio, cv=LeaveOneOut())
        loo_mae = float(np.median(np.abs(loo_pred - y_ratio)))
        print(f"{label:<18}{loo_mae:>14.3f}{corr:>10.3f}{np.median(logr):>13.3f}{zero_share:>10.1%}{full_share:>11.1%}")

    print("\n== 4. fit on the kernel's 250 answers, predict the 25, compare with gold ==")
    mask = kernel_ratio.notna().to_numpy()
    X250k = X250[mask]
    y_kernel = kernel_ratio.to_numpy(float)[mask]
    gold_ratio = ratio_target(small, "gold").to_numpy(float)
    gold_safe = small["gold"].to_numpy(float)
    print(f"{'model':<18}{'25 vs gold corr':>16}{'25 med|log|':>13}{'25 exact':>10}")
    for label, model in model_zoo():
        try:
            model.fit(X250k, y_kernel)
            predicted = safe_from_ratio(small, model.predict(X25))
        except Exception as exc:
            print(f"{label:<18} failed: {type(exc).__name__}")
            continue
        corr = float(pd.Series(predicted).corr(pd.Series(gold_safe), method="spearman"))
        with np.errstate(divide="ignore", invalid="ignore"):
            logr = np.abs(np.log((predicted + 1.0) / (gold_safe + 1.0)))
        exact = int(np.sum(np.abs(predicted - gold_safe) <= 0.01))
        print(f"{label:<18}{corr:>16.3f}{np.median(logr):>13.3f}{exact:>7}/{len(gold_safe)}")


if __name__ == "__main__":
    main()
