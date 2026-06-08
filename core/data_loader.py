"""MT5 OHLCV CSV loader + validator.

Expected format (comma or tab separated):
    Date,Time,Open,High,Low,Close,Volume
    2024.01.02,01:00,2063.45,2064.12,2062.80,2063.90,342

Phase 2 additions:
  - validate_dataframe() returns a structured validation report
  - OHLC integrity checks (High >= Low, High >= Open/Close, etc.)
  - Duplicate timestamp removal

Phase 2 (multi-file):
  - combine_dataframes() merges multiple DataFrames, deduplicates, sorts
  - save_dataframe() writes back to MT5 CSV format so load_csv() can reload it
  - Minimum bar count enforcement
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

_REQUIRED = {"date", "time", "open", "high", "low", "close", "volume"}
_MIN_BARS = 60   # need enough history to form swing points


def load_csv(source: str | Path | bytes | io.IOBase) -> pd.DataFrame:
    """
    Parse an MT5 CSV export and return a clean OHLCV DataFrame.

    Raises ValueError on unrecoverable parse failures.
    Silently drops rows with non-numeric OHLCV or duplicate timestamps.
    """
    if isinstance(source, (str, Path)):
        raw = pd.read_csv(source, sep=None, engine="python")
    elif isinstance(source, bytes):
        raw = pd.read_csv(io.BytesIO(source), sep=None, engine="python")
    else:
        raw = pd.read_csv(source, sep=None, engine="python")

    raw.columns = [c.strip().lower() for c in raw.columns]

    missing = _REQUIRED - set(raw.columns)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {sorted(missing)}. "
            "Expected: Date, Time, Open, High, Low, Close, Volume"
        )

    # Build datetime index
    date_str = raw["date"].astype(str).str.replace(".", "-", regex=False)
    time_str = raw["time"].astype(str)
    raw["datetime"] = pd.to_datetime(date_str + " " + time_str, errors="coerce")

    if raw["datetime"].isna().all():
        raise ValueError(
            "Could not parse any datetime values — check Date/Time column format."
        )

    df = (
        raw.set_index("datetime")
        .drop(columns=["date", "time"])
        .rename(columns=str.capitalize)
    )

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    if df.empty:
        raise ValueError("No valid OHLCV rows found after parsing.")

    return df


def validate_dataframe(df: pd.DataFrame) -> dict:
    """
    Run quality checks on a loaded DataFrame.

    Returns
    -------
    {
        "valid"      : bool,
        "row_count"  : int,
        "date_range" : str,
        "errors"     : list[str],   # blocks analysis
        "warnings"   : list[str],   # informational only
    }
    """
    errors: list[str] = []
    warnings: list[str] = []

    row_count = len(df)

    # ── Minimum rows ──────────────────────────────────────────────────────────
    if row_count < _MIN_BARS:
        errors.append(
            f"Too few bars: {row_count} loaded (minimum {_MIN_BARS} required "
            "to form swing points)."
        )

    # ── NaN / zero prices ─────────────────────────────────────────────────────
    nan_counts = df[["Open", "High", "Low", "Close"]].isna().sum()
    if nan_counts.any():
        warnings.append(f"NaN values present: {nan_counts[nan_counts > 0].to_dict()}")

    zero_rows = (df[["Open", "High", "Low", "Close"]] == 0).any(axis=1).sum()
    if zero_rows:
        warnings.append(f"{zero_rows} candles contain a zero price.")

    # ── OHLC integrity ────────────────────────────────────────────────────────
    tol = 1e-6
    bad_high = (df["High"] < df[["Open", "Close"]].max(axis=1) - tol).sum()
    bad_low  = (df["Low"]  > df[["Open", "Close"]].min(axis=1) + tol).sum()
    bad_hl   = (df["High"] < df["Low"] - tol).sum()

    if bad_hl:
        errors.append(f"{bad_hl} candles where High < Low (corrupt data).")
    if bad_high:
        warnings.append(
            f"{bad_high} candles where High < max(Open, Close) "
            "(may indicate rounding in source data)."
        )
    if bad_low:
        warnings.append(
            f"{bad_low} candles where Low > min(Open, Close) "
            "(may indicate rounding in source data)."
        )

    # ── Price range sanity (XAUUSD roughly 500–5000) ─────────────────────────
    price_min = float(df["Close"].min())
    price_max = float(df["Close"].max())
    if price_min < 500 or price_max > 5_000:
        warnings.append(
            f"Unusual price range {price_min:.2f}–{price_max:.2f}. "
            "Expected XAUUSD in the 500–5000 range."
        )

    # ── Duplicate timestamps ──────────────────────────────────────────────────
    dups = df.index.duplicated().sum()
    if dups:
        warnings.append(f"{dups} duplicate timestamps were automatically removed.")

    # ── Gaps (missing candles) ────────────────────────────────────────────────
    if row_count >= 2:
        diffs = df.index.to_series().diff().dropna()
        median_gap = diffs.median()
        large_gaps = (diffs > median_gap * 5).sum()
        if large_gaps:
            warnings.append(
                f"{large_gaps} large time gaps detected (weekend/holiday gaps are normal)."
            )

    date_range = f"{df.index[0]}" if row_count == 0 else f"{df.index[0]} → {df.index[-1]}"

    return {
        "valid": len(errors) == 0,
        "row_count": row_count,
        "date_range": date_range,
        "errors": errors,
        "warnings": warnings,
    }


# ── Multi-file helpers ────────────────────────────────────────────────────────

def combine_dataframes(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    """
    Merge a list of OHLCV DataFrames into one sorted, deduplicated DataFrame.
    Overlapping timestamps keep the row from the first file that contained them.
    """
    if not dfs:
        raise ValueError("No DataFrames to combine.")
    if len(dfs) == 1:
        return dfs[0].copy()

    combined = pd.concat(dfs)
    combined = combined[~combined.index.duplicated(keep="first")]
    combined = combined.sort_index()
    return combined


# ── Timeframe detection ───────────────────────────────────────────────────────

_KNOWN_TF = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN"]


def detect_timeframe(filename: str) -> str | None:
    """
    Detect the timeframe label from an MT5 export filename.

    Supports: XAUUSD_M1_OHLCV.csv → M1
              XAUUSD_H4_OHLCV.csv → H4
              GOLD-D1-DATA.csv    → D1

    Returns None when no recognised timeframe token is found.
    """
    import re
    stem = re.sub(r"[\s\-\.]", "_", Path(filename).stem.upper())
    for tf in sorted(_KNOWN_TF, key=len, reverse=True):  # longest first (M15 before M1)
        if re.search(r"(?:^|_)" + re.escape(tf) + r"(?:_|$)", stem):
            return tf
    return None


def save_dataframe(df: pd.DataFrame, path: "Path") -> None:
    """
    Write a DataFrame back to MT5 CSV format so load_csv() can reload it.
    Columns written: Date, Time, Open, High, Low, Close, Volume
    """
    from pathlib import Path as _Path
    _Path(path).parent.mkdir(parents=True, exist_ok=True)

    idx = pd.to_datetime(df.index)
    out = pd.DataFrame({
        "Date":   idx.strftime("%Y.%m.%d"),
        "Time":   idx.strftime("%H:%M"),
        "Open":   df["Open"].values,
        "High":   df["High"].values,
        "Low":    df["Low"].values,
        "Close":  df["Close"].values,
        "Volume": df["Volume"].values,
    })
    out.to_csv(path, index=False)
