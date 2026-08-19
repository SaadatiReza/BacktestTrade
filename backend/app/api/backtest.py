from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.backtest.engine import compute_stats, run_backtest
from app.data_provider import DataProviderError, fetch_and_cache, load_candles
from app.db import get_db
from app.models import BacktestRun, Trade
from app.schemas import BacktestRequest, BacktestResponse

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("", response_model=BacktestResponse)
def run(req: BacktestRequest, db: Session = Depends(get_db)):
    if not req.use_cached_data_only:
        try:
            fetch_and_cache(db, req.symbol, req.interval, req.start, req.end)
        except DataProviderError as e:
            raise HTTPException(status_code=400, detail=str(e))

    df = load_candles(db, req.symbol, req.interval, req.start, req.end)
    if len(df) < 30:
        raise HTTPException(status_code=400, detail=f"Not enough candles ({len(df)}) in range to backtest.")

    trades = run_backtest(df, req.config)
    stats = compute_stats(trades)

    run_row = BacktestRun(
        symbol=req.symbol,
        interval=req.interval,
        start=req.start,
        end=req.end,
        config=req.config.model_dump(),
        stats=stats,
    )
    db.add(run_row)
    db.flush()

    for t in trades:
        s = t.signal
        db.add(
            Trade(
                run_id=run_row.id,
                direction=s.direction,
                context=s.context,
                entry_mode=s.entry_mode,
                spike_start_time=s.structure.spike.start_time,
                spike_end_time=s.structure.spike.end_time,
                level_price=s.structure.level_price,
                entry_time=s.entry_time,
                entry_price=s.entry_price,
                sl_price=s.sl_price,
                tp1_price=s.tp1_price,
                tp2_price=s.tp2_price,
                exit_time=t.exit_time,
                exit_price=t.exit_price,
                r_multiple=t.r_multiple,
                result=t.result,
            )
        )
    db.commit()
    db.refresh(run_row)

    return BacktestResponse(
        run_id=run_row.id,
        symbol=req.symbol,
        interval=req.interval,
        stats=stats,
        trades=run_row.trades,
    )


@router.get("/{run_id}", response_model=BacktestResponse)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run_row = db.get(BacktestRun, run_id)
    if not run_row:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return BacktestResponse(
        run_id=run_row.id,
        symbol=run_row.symbol,
        interval=run_row.interval,
        stats=run_row.stats,
        trades=run_row.trades,
    )
