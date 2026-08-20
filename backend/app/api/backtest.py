from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

import app.strategy  # noqa: F401 -- importing the package registers every built-in strategy
from app.backtest.engine import compute_stats, run_backtest
from app.data_provider import DataProviderError, fetch_and_cache, load_candles
from app.db import get_db
from app.models import BacktestRun, Trade
from app.schemas import BacktestRequest, BacktestResponse, StrategyInfo
from app.strategy.base import get_strategy, list_strategies

router = APIRouter(tags=["backtest"])


@router.get("/strategies", response_model=list[StrategyInfo])
def get_strategies():
    return [
        StrategyInfo(
            id=s.id,
            name=s.name,
            description=s.description,
            config_schema=s.config_model.model_json_schema(),
        )
        for s in list_strategies()
    ]


@router.post("/backtest", response_model=BacktestResponse)
def run(req: BacktestRequest, db: Session = Depends(get_db)):
    strategy = get_strategy(req.strategy)
    if strategy is None:
        known = ", ".join(s.id for s in list_strategies())
        raise HTTPException(status_code=400, detail=f"Unknown strategy '{req.strategy}'. Known strategies: {known}")

    try:
        strategy_config = strategy.config_model(**req.strategy_config)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())

    if not req.use_cached_data_only:
        try:
            fetch_and_cache(db, req.symbol, req.interval, req.start, req.end)
        except DataProviderError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # a fixed past date range + identical config always deterministically
    # produces the same result (historical candles don't change), so re-running
    # an unchanged backtest reuses the stored run instead of logging a fresh
    # duplicate set of trades. Only the cheap indexed columns are filtered in
    # SQL; the config dict itself is compared in Python for portability across
    # Postgres (JSONB) and SQLite (plain JSON/TEXT).
    merged_config = {"strategy_config": strategy_config.model_dump(), "execution": req.execution.model_dump()}
    candidates = (
        db.query(BacktestRun)
        .filter(
            BacktestRun.symbol == req.symbol,
            BacktestRun.interval == req.interval,
            BacktestRun.start == req.start,
            BacktestRun.end == req.end,
            BacktestRun.strategy_id == strategy.id,
        )
        .order_by(BacktestRun.id.desc())
        .all()
    )
    existing_run = next((r for r in candidates if r.config == merged_config), None)
    if existing_run is not None:
        return BacktestResponse(
            run_id=existing_run.id,
            symbol=existing_run.symbol,
            interval=existing_run.interval,
            strategy=existing_run.strategy_id,
            stats=existing_run.stats,
            trades=existing_run.trades,
        )

    df = load_candles(db, req.symbol, req.interval, req.start, req.end)
    if len(df) < 30:
        raise HTTPException(status_code=400, detail=f"Not enough candles ({len(df)}) in range to backtest.")

    trades = run_backtest(df, strategy, strategy_config, req.execution)
    stats = compute_stats(trades)

    run_row = BacktestRun(
        symbol=req.symbol,
        interval=req.interval,
        start=req.start,
        end=req.end,
        strategy_id=strategy.id,
        config=merged_config,
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
                entry_time=s.entry_time,
                entry_price=s.entry_price,
                sl_price=s.sl_price,
                tp1_price=t.tp1_price,
                tp2_price=t.tp2_price,
                exit_time=t.exit_time,
                exit_price=t.exit_price,
                r_multiple=t.r_multiple,
                result=t.result,
                meta=s.meta,
            )
        )
    db.commit()
    db.refresh(run_row)

    return BacktestResponse(
        run_id=run_row.id,
        symbol=req.symbol,
        interval=req.interval,
        strategy=strategy.id,
        stats=stats,
        trades=run_row.trades,
    )


@router.get("/backtest/{run_id}", response_model=BacktestResponse)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run_row = db.get(BacktestRun, run_id)
    if not run_row:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return BacktestResponse(
        run_id=run_row.id,
        symbol=run_row.symbol,
        interval=run_row.interval,
        strategy=run_row.strategy_id,
        stats=run_row.stats,
        trades=run_row.trades,
    )
