from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.data_provider import DataProviderError, fetch_and_cache, load_candles
from app.db import get_db
from app.schemas import FetchDataRequest

router = APIRouter(prefix="/data", tags=["data"])


@router.post("/fetch")
def fetch_data(req: FetchDataRequest, db: Session = Depends(get_db)):
    try:
        count = fetch_and_cache(db, req.symbol, req.interval, req.start, req.end)
    except DataProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"fetched": count}


@router.get("/candles")
def get_candles(symbol: str, interval: str, start: str, end: str, db: Session = Depends(get_db)):
    from datetime import datetime

    df = load_candles(db, symbol, interval, datetime.fromisoformat(start), datetime.fromisoformat(end))
    return df.assign(time=df["time"].astype(str)).to_dict(orient="records") if len(df) else []
