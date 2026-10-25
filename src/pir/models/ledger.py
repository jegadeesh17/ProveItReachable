"""Spend ledger state (ARCHITECTURE section 6.6)."""

import math
from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from pir.models import Pool, Strict

Spender = Literal["hosted", "local"]  # serve = hosted; scan, eval, record = local (section 11.2)


class PoolState(Strict):
    spent_usd: float = Field(ge=0)
    reserved_usd: float = Field(ge=0)


class DayState(Strict):
    day: date
    start_spent_usd: dict[Pool, float]


class SpenderState(Strict):
    allowance_usd: dict[Pool, float]
    pools: dict[Pool, PoolState]
    day: DayState | None


class Reservation(Strict):
    scan_id: str
    spender: Spender
    created_at: datetime
    amounts: dict[Pool, float]


class CasCheck(Strict):
    verified_at: datetime
    hub_version: str


class LedgerState(Strict):
    schema_version: Literal[2]
    caps_usd: dict[Pool, float]
    spenders: dict[Spender, SpenderState]
    reservations: list[Reservation]
    cas_verified: CasCheck | None
    updated_at: datetime

    @model_validator(mode="after")
    def _check(self) -> "LedgerState":
        for pool, cap in self.caps_usd.items():
            if cap < 0:
                raise ValueError("caps must not be negative")
            total = sum(s.allowance_usd.get(pool, 0.0) for s in self.spenders.values())
            if not math.isclose(total, cap, abs_tol=1e-6):
                raise ValueError(f"allowances for {pool} must sum to its cap")
        return self
