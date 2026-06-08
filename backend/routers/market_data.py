from fastapi import APIRouter, HTTPException, Query
from backend.services.data_service import fetch_ohlcv
from backend.schemas.market import MarketDataResponse, OHLCVBar

router = APIRouter()

PERIOD_DESC = "1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max"
INTERVAL_DESC = "1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo"


@router.get("/{symbol}", response_model=MarketDataResponse)
def get_market_data(
    symbol: str,
    period: str = Query("1y", description=PERIOD_DESC),
    interval: str = Query("1d", description=INTERVAL_DESC),
):
    try:
        df = fetch_ohlcv(symbol.upper(), period, interval)
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
        return MarketDataResponse(symbol=symbol.upper(), period=period, interval=interval, bars=bars)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
