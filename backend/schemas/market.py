from pydantic import BaseModel
from typing import List, Optional


class OHLCVBar(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataResponse(BaseModel):
    symbol: str
    period: str
    interval: str
    bars: List[OHLCVBar]
