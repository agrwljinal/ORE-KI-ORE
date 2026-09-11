# scripts/train_shortfall_model.py
# OWNER: Member 3 (Zahra) -- Shortfall Predictor
#
# Trains the 13-feature linear regression described in the M3 spec and saves
# it to models/shortfall_regression_model.pkl so modules/prediction.py can
# load it at runtime without retraining on every request.
#
# Feature list (13, exactly as scoped):
#     Same-day (3):  rainfall_norm, equipment_downtime_norm, labor_availability_proxy
#     Rolling  (4):  rain_3d_norm, downtime_3d_norm, rain_7d_norm, downtime_7d_norm
#     Mine ID  (6):  one-hot dummies for 6 mines (7th mine = baseline reference)
#
# Target: output_ratio = actual_rom_tonnes / target_rom_tonnes
#
# Method: plain ordinary least squares via numpy's lstsq (normal-equation style
# solve), not scikit-learn -- keeps the dependency footprint at what
# requirements.txt already has (numpy/pandas) and keeps every coefficient
# inspectable, which matters for the "fully explainable" pitch.
#
# Honesty built in on purpose:
#   - We report BOTH the pooled test R^2 (inflated -- mostly mine-level
#     averaging, since mine identity alone predicts a lot) AND the honest
#     per-mine test R^2 (fit + evaluated within each mine separately). The
#     per-mine number is the one that should be quoted to judges.
#   - Rolling downtime coefficients have previously come out with a
#     counter-intuitive (positive) sign due to multicollinearity between
#     rainfall/downtime/blast columns. We record the raw coefficients as-is
#     (no sign flipping) and flag this in the saved metadata so
#     modules/prediction.py can surface the caveat rather than hide it.
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
DATA_PATH = REPO_ROOT / "data" / "processed_production.csv"
MODEL_OUT_PATH = REPO_ROOT / "models" / "shortfall_regression_model.pkl"

# Normalization caps. Chosen from the real v3 data range (see recap doc),
# with a little headroom so a slightly-worse-than-observed slider value
# doesn't get silently clipped to 1.0 right away.
RAIN_NORM_CAP_MM = 100.0        # observed max ~95.1 mm/day
DOWNTIME_NORM_CAP_HRS = 14.0    # observed max ~12 hrs/day
BLAST_DELAY_NORM_CAP_MIN = 180.0  # observed max ~164 min

FEATURE_COLUMNS = [
    "rainfall_norm",
    "equipment_downtime_norm",
    "labor_availability_proxy",
    "rain_3d_norm",
    "downtime_3d_norm",
    "rain_7d_norm",
    "downtime_7d_norm",
    # mine one-hots appended after this list is built (6 of 7 mines)
]


def build_features(df: pd.DataFrame, mine_list: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["rainfall_norm"] = (df["rainfall_mm"] / RAIN_NORM_CAP_MM).clip(0, 1)
    out["equipment_downtime_norm"] = (df["equipment_downtime_hours"] / DOWNTIME_NORM_CAP_HRS).clip(0, 1)
    # blast_delay_minutes proxies crew/permit issues -> higher delay = less
    # effective labor availability, so we invert it into an availability proxy.
    out["labor_availability_proxy"] = 1.0 - (df["blast_delay_minutes"] / BLAST_DELAY_NORM_CAP_MIN).clip(0, 1)
    out["rain_3d_norm"] = (df["rain_3d_mm"] / RAIN_NORM_CAP_MM).clip(0, 1)
    out["downtime_3d_norm"] = (df["downtime_3d_hours"] / DOWNTIME_NORM_CAP_HRS).clip(0, 1)
    out["rain_7d_norm"] = (df["rain_7d_mm"] / RAIN_NORM_CAP_MM).clip(0, 1)
    out["downtime_7d_norm"] = (df["downtime_7d_hours"] / DOWNTIME_NORM_CAP_HRS).clip(0, 1)
    for mine in mine_list[1:]:  # drop first mine as baseline reference
        out[f"mine_{mine}"] = (df["mine_name"] == mine).astype(float)
    return out


def per_mine_split(df: pd.DataFrame, test_frac: float = 0.2):
    """Each mine's own last `test_frac` of dates go to test, so every mine
    (including Dongri Buzurg's out-of-range 2009-10 dates) is represented in
    both train and test. Mirrors the split already used for the earlier
    per-mine diagnostic model.
    """
    train_idx, test_idx = [], []
    for mine, g in df.groupby("mine_name"):
        g = g.sort_values("date")
        n_test = max(1, int(round(len(g) * test_frac)))
        train_idx.extend(g.index[:-n_test])
        test_idx.extend(g.index[-n_test:])
    return df.loc[train_idx], df.loc[test_idx]


def fit_ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Ordinary least squares with intercept via lstsq (normal-equation
    style). Returns coefficients where coeffs[0] is the intercept.
    """
    X_design = np.column_stack([np.ones(len(X)), X])
    coeffs, *_ = np.linalg.lstsq(X_design, y, rcond=None)
    return coeffs


def r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    if ss_tot == 0:
        return 0.0
    return 1.0 - ss_res / ss_tot


def main() -> None:
    df = pd.read_csv(DATA_PATH)
    df["date"] = pd.to_datetime(df["date"])
    df["output_ratio"] = (df["actual_rom_tonnes"] / df["target_rom_tonnes"]).clip(0, 2)

    mine_list = sorted(df["mine_name"].unique().tolist())
    assert len(mine_list) == 7, f"expected 7 MOIL mines, found {len(mine_list)}: {mine_list}"

    feature_cols = FEATURE_COLUMNS + [f"mine_{m}" for m in mine_list[1:]]
    assert len(feature_cols) == 13, f"expected exactly 13 features, built {len(feature_cols)}"

    train_df, test_df = per_mine_split(df)

    X_train = build_features(train_df, mine_list)[feature_cols].to_numpy(dtype=float)
    y_train = train_df["output_ratio"].to_numpy(dtype=float)
    X_test = build_features(test_df, mine_list)[feature_cols].to_numpy(dtype=float)
    y_test = test_df["output_ratio"].to_numpy(dtype=float)

    coeffs = fit_ols(X_train, y_train)
    intercept, weights = float(coeffs[0]), coeffs[1:]

    y_pred_test = intercept + X_test @ weights
    pooled_test_r2 = r2_score(y_test, y_pred_test)

    y_pred_train = intercept + X_train @ weights
    mae_test = float(np.mean(np.abs(y_test - y_pred_test)))

    # Honest per-mine R^2: evaluate the SAME pooled model separately within
    # each mine's own test rows (not a separate per-mine model this time --
    # this checks how well the single deployed model generalizes per mine).
    per_mine_r2 = {}
    for mine in mine_list:
        mask = test_df["mine_name"].values == mine
        if mask.sum() < 2:
            continue
        per_mine_r2[mine] = round(r2_score(y_test[mask], y_pred_test[mask]), 4)
    avg_per_mine_r2 = float(np.mean(list(per_mine_r2.values()))) if per_mine_r2 else None

    # Multicollinearity honesty check: does either rolling-downtime
    # coefficient come out positive (counter-intuitive sign)?
    coeff_map = dict(zip(feature_cols, weights.tolist()))
    counter_intuitive_signs = [
        name for name in ("equipment_downtime_norm", "downtime_3d_norm", "downtime_7d_norm")
        if coeff_map.get(name, 0.0) > 0
    ]

    bundle = {
        "model_type": "linear_regression_13_feature",
        "target": "output_ratio (actual_rom_tonnes / target_rom_tonnes)",
        "feature_order": feature_cols,
        "intercept": intercept,
        "coefficients": coeff_map,
        "mine_list": mine_list,
        "baseline_mine": mine_list[0],
        "normalization": {
            "rain_cap_mm": RAIN_NORM_CAP_MM,
            "downtime_cap_hrs": DOWNTIME_NORM_CAP_HRS,
            "blast_delay_cap_min": BLAST_DELAY_NORM_CAP_MIN,
        },
        "metrics": {
            "pooled_test_r2": round(pooled_test_r2, 4),
            "avg_per_mine_test_r2": round(avg_per_mine_r2, 4) if avg_per_mine_r2 is not None else None,
            "per_mine_test_r2": per_mine_r2,
            "test_mae_output_ratio": round(mae_test, 4),
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        },
        "honesty_notes": [
            "pooled_test_r2 is inflated relative to avg_per_mine_test_r2 because mine-identity "
            "dummies alone explain a lot of the pooled variance; avg_per_mine_test_r2 is the "
            "defensible number to quote.",
        ] + (
            [
                f"Counter-intuitive positive coefficient(s) on {counter_intuitive_signs}: classic "
                "multicollinearity between rainfall/downtime/blast-delay columns, not a claim that "
                "more downtime increases output. Interpret coefficient magnitude, not sign, for these."
            ] if counter_intuitive_signs else []
        ),
        "trained_on": "data/processed_production.csv (v3, 7 MOIL mines, daily breakdown synthetic, "
                       "annual totals sourced from IBM MCDR filings)",
    }

    MODEL_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_OUT_PATH, "wb") as f:
        pickle.dump(bundle, f)

    print(f"Saved model to {MODEL_OUT_PATH}")
    print(f"Pooled test R^2:        {bundle['metrics']['pooled_test_r2']}")
    print(f"Avg per-mine test R^2:  {bundle['metrics']['avg_per_mine_test_r2']}  (honest metric)")
    print(f"Test MAE (output_ratio): {bundle['metrics']['test_mae_output_ratio']}")
    print(f"Per-mine test R^2: {per_mine_r2}")
    if counter_intuitive_signs:
        print(f"NOTE: counter-intuitive positive coefficients on {counter_intuitive_signs} (multicollinearity)")


if __name__ == "__main__":
    main()