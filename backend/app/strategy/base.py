"""The strategy plugin contract.

To add a new strategy: write a module under app/strategy/ that (1) defines
a Pydantic config model for its own parameters, (2) implements
`generate_signals(df, config) -> list[Signal]`, and (3) calls
`register_strategy(...)` once at import time. Then import that module from
app/strategy/__init__.py so it registers itself on startup. Nothing else in
the app (engine, API, DB) needs to change -- the backtest engine applies the
same generic SL/TP/holding-period/risk rules (ExecutionConfig) to every
strategy's signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Type

import pandas as pd
from pydantic import BaseModel

from app.strategy.types import Signal


@dataclass
class StrategyDefinition:
    id: str
    name: str
    description: str
    config_model: Type[BaseModel]
    generate_signals: Callable[[pd.DataFrame, BaseModel], list[Signal]]


_REGISTRY: dict[str, StrategyDefinition] = {}


def register_strategy(definition: StrategyDefinition) -> None:
    if definition.id in _REGISTRY:
        raise ValueError(f"Strategy '{definition.id}' is already registered")
    _REGISTRY[definition.id] = definition


def get_strategy(strategy_id: str) -> StrategyDefinition | None:
    return _REGISTRY.get(strategy_id)


def list_strategies() -> list[StrategyDefinition]:
    return list(_REGISTRY.values())
