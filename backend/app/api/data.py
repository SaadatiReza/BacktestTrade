from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.data_provider import DataProviderError, fetch_and_cache, load_candles
from app.db import get_db
from app.schemas import FetchDataRequest
from app.timeutil import to_naive_utc

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

    start_dt = to_naive_utc(datetime.fromisoformat(start))
    end_dt = to_naive_utc(datetime.fromisoformat(end))
    df = load_candles(db, symbol, interval, start_dt, end_dt)
    return df.assign(time=df["time"].astype(str)).to_dict(orient="records") if len(df) else []
