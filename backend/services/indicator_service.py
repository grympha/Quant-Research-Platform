import math
import pandas as pd
import numpy as np


def _clean(series: pd.Series) -> list:
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(v, 6) for v in series.tolist()]


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0):
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


VALID_INDICATORS = ["sma20", "sma50", "sma200", "ema12", "ema26", "rsi", "macd", "bbands"]


def compute_indicators(df: pd.DataFrame, requested: list) -> dict:
    close = df["Close"]
    result = {}

    for ind in requested:
        ind = ind.lower().strip()
        if ind == "sma20":
            result["SMA_20"] = _clean(sma(close, 20))
        elif ind == "sma50":
            result["SMA_50"] = _clean(sma(close, 50))
        elif ind == "sma200":
            result["SMA_200"] = _clean(sma(close, 200))
        elif ind == "ema12":
            result["EMA_12"] = _clean(ema(close, 12))
        elif ind == "ema26":
            result["EMA_26"] = _clean(ema(close, 26))
        elif ind == "rsi":
            result["RSI_14"] = _clean(rsi(close, 14))
        elif ind == "macd":
            m, s, h = macd(close)
            result["MACD"] = _clean(m)
            result["MACD_Signal"] = _clean(s)
            result["MACD_Hist"] = _clean(h)
        elif ind == "bbands":
            u, mid, lo = bollinger_bands(close)
            result["BB_Upper"] = _clean(u)
            result["BB_Middle"] = _clean(mid)
            result["BB_Lower"] = _clean(lo)

    return result
