"""MT5 OHLCV CSV loader.

Expected format (tab or comma-separated):
    Date,Time,Open,High,Low,Close,Volume
    2024.01.02,01:00,2063.45,2064.12,2062.80,2063.90,342
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd


_REQUIRED = {"date", "time", "open", "high", "low", "close", "volume"}


def load_csv(source: str | Path | bytes | io.IOBase) -> pd.DataFrame:
    """Parse an MT5 CSV export and return a clean OHLCV DataFrame indexed by Datetime."""

    if isinstance(source, (str, Path)):
        raw = pd.read_csv(source, sep=None, engine="python")
    elif isinstance(source, bytes):
        raw = pd.read_csv(io.BytesIO(source), sep=None, engine="python")
    else:
        raw = pd.read_csv(source, sep=None, engine="python")

    # Normalise column names
    raw.columns = [c.strip().lower() for c in raw.columns]

    missing = _REQUIRED - set(raw.columns)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {sorted(missing)}. "
            f"Expected: Date, Time, Open, High, Low, Close, Volume"
        )

    # Build datetime index from Date + Time columns
    # MT5 uses "2024.01.02" or "2024-01-02" date formats
    date_str = raw["date"].astype(str).str.replace(".", "-", regex=False)
    time_str = raw["time"].astype(str)
    raw["datetime"] = pd.to_datetime(date_str + " " + time_str, errors="coerce")

    invalid = raw["datetime"].isna().sum()
    if invalid == len(raw):
        raise ValueError("Could not parse any datetime values — check Date/Time column format.")

    df = (
        raw.set_index("datetime")
        .drop(columns=["date", "time"])
        .rename(columns=str.capitalize)
    )

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()

    if df.empty:
        raise ValueError("No valid OHLCV rows found after parsing.")

    return df
