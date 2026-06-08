from fastapi import APIRouter, HTTPException, Query
from backend.services.data_service import fetch_ohlcv
from backend.services.indicator_service import compute_indicators, VALID_INDICATORS
from backend.schemas.market import OHLCVBar

router = APIRouter()


@router.get("/{symbol}")
def get_indicators(
    symbol: str,
    period: str = Query("1y"),
    interval: str = Query("1d"),
    indicators: str = Query(
        "sma20,sma50,rsi,macd,bbands",
        description=f"Comma-separated subset of: {', '.join(VALID_INDICATORS)}",
    ),
):
    try:
        df = fetch_ohlcv(symbol.upper(), period, interval)
        requested = [i.strip() for i in indicators.split(",") if i.strip()]
        indicator_data = compute_indicators(df, requested)

        bars = [
            OHLCVBar(
                timestamp=str(idx.date()) if hasattr(idx, "date") else str(idx),
                open=round(float(row["Open"]), 4),
                high=round(float(row["High"]), 4),
                low=round(float(row["Low"]), 4),
                close=round(float(row["Close"]), 4),
                volume=float(row["Volume"]),
            )
            for idx, row in df.iterrows()
        ]

        return {
            "symbol": symbol.upper(),
            "period": period,
            "interval": interval,
            "bars": [b.model_dump() for b in bars],
            "indicators": indicator_data,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
