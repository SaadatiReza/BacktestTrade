from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.backtest import router as backtest_router
from app.api.data import router as data_router

# Schema is managed by Alembic (`alembic upgrade head`), run explicitly
# before starting the server -- not auto-applied on import, so a schema
# change is always a deliberate, reviewable migration rather than a silent
# side effect of starting uvicorn. See README.md for the setup flow.

app = FastAPI(title="SP2L Backtester")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data_router)
app.include_router(backtest_router)


@app.get("/health")
def health():
    return {"status": "ok"}
