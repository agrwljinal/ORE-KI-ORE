"""Train the direct actual-ROM output model used by the dashboard."""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
DATA_PATH = REPO_ROOT / "data" / "processed_production.csv"
MODEL_OUT_PATH = REPO_ROOT / "models" / "shortfall_regression_model.pkl"

# Real v4 production fields. Rolling values are trailing historical features.
NUMERIC_FEATURES = [
    "rainfall_mm", "rain_3d_mm", "rain_7d_mm",
    "soil_moisture_pct", "soil_moisture_3d_avg", "soil_moisture_7d_avg",
    "equipment_downtime_hours", "blast_delay_minutes",
]


def per_mine_split(df: pd.DataFrame, test_frac: float = 0.2):
    train_idx, test_idx = [], []
    for _, group in df.groupby("mine_name"):
        group = group.sort_values("date")
        n_test = max(1, int(round(len(group) * test_frac)))
        train_idx.extend(group.index[:-n_test])
        test_idx.extend(group.index[-n_test:])
    return df.loc[train_idx], df.loc[test_idx]


def r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_total = float(np.sum((y_true - np.mean(y_true)) ** 2))
    return 0.0 if ss_total == 0 else 1.0 - float(np.sum((y_true - y_pred) ** 2)) / ss_total


def main() -> None:
    df = pd.read_csv(DATA_PATH)
    required = set(NUMERIC_FEATURES + ["date", "mine_name", "actual_rom_tonnes"])
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"v4 production data is required; missing columns: {missing}")
    df["date"] = pd.to_datetime(df["date"])
    mine_list = sorted(df["mine_name"].dropna().unique().tolist())
    train_df, test_df = per_mine_split(df)

    # Training-only caps avoid leaking the future test period into scaling.
    caps = train_df[NUMERIC_FEATURES].astype(float).quantile(0.99).replace(0, 1.0).to_dict()

    def vector(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame[NUMERIC_FEATURES].astype(float).clip(lower=0)
        out = out.divide(pd.Series(caps), axis="columns").clip(0, 1.5)
        for mine in mine_list[1:]:
            out[f"mine_{mine}"] = (frame["mine_name"] == mine).astype(float)
        return out

    feature_order = list(vector(train_df).columns)
    x_train = vector(train_df)[feature_order].to_numpy(float)
    x_test = vector(test_df)[feature_order].to_numpy(float)
    y_train = train_df["actual_rom_tonnes"].to_numpy(float)
    y_test = test_df["actual_rom_tonnes"].to_numpy(float)
    coefs, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(x_train)), x_train]), y_train, rcond=None)
    intercept, weights = float(coefs[0]), coefs[1:]
    y_pred = intercept + x_test @ weights

    bundle = {
        "model_type": "ols_direct_actual_rom_regression",
        "target": "actual_rom_tonnes",
        "feature_order": feature_order,
        "numeric_feature_caps": {key: float(value) for key, value in caps.items()},
        "intercept": intercept,
        "coefficients": dict(zip(feature_order, weights.tolist())),
        "mine_list": mine_list,
        "baseline_mine": mine_list[0],
        "metrics": {
            "test_mae_tonnes": round(float(np.mean(np.abs(y_test - y_pred))), 2),
            "test_rmse_tonnes": round(float(np.sqrt(np.mean((y_test - y_pred) ** 2))), 2),
            "test_r2": round(r2_score(y_test, y_pred), 4),
            "n_train": int(len(train_df)), "n_test": int(len(test_df)),
        },
        "honesty_notes": [
            "The model predicts actual_rom_tonnes directly; target, attendance and ore grade are not ML features.",
            "v4 soil-moisture values are synthetic demo data and must be replaced with a real satellite product for deployment.",
        ],
    }
    MODEL_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_OUT_PATH, "wb") as handle:
        pickle.dump(bundle, handle)
    print(f"Saved direct-output model to {MODEL_OUT_PATH}")
    print(bundle["metrics"])


if __name__ == "__main__":
    main()
