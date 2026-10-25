"""MemorySpendLedger: an in-memory `SpendLedger` with injectable conflicts and failures."""

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime

from pir.models import Pool
from pir.models.ledger import LedgerState, PoolState, SpenderState
from pir.spend import LedgerConflict, LedgerUnavailable

POOLS: tuple[Pool, ...] = ("nebius", "tavily")


def make_state(
    now: datetime,
    allowances: Mapping[str, Mapping[Pool, float]],
    *,
    spent: Mapping[str, Mapping[Pool, float]] | None = None,
) -> LedgerState:
    """A ledger state with one spender per key of `allowances`; caps are the allowance sums."""
    spent = spent or {}
    caps = {p: sum(a.get(p, 0.0) for a in allowances.values()) for p in POOLS}
    spenders = {
        name: SpenderState(
            allowance_usd={p: float(allow.get(p, 0.0)) for p in POOLS},
            pools={
                p: PoolState(spent_usd=float(spent.get(name, {}).get(p, 0.0)), reserved_usd=0.0)
                for p in POOLS
            },
            day=None,
        )
        for name, allow in allowances.items()
    }
    return LedgerState(
        schema_version=2,
        caps_usd=caps,
        spenders=spenders,  # type: ignore[arg-type]
        reservations=[],
        cas_verified=None,
        updated_at=now,
    )


class MemorySpendLedger:
    """Revision is a counter. Inject faults with `conflicts` (the next N saves lose a race to
    another writer), `fail_loads` and `fail_saves` (raise `LedgerUnavailable`; a number counts
    down, `True` fails forever). `conflict_hook(ledger)` runs when a conflict is injected, so a test
    can change `ledger.state` as the other writer would. `yield_io` hands control to the event
    loop inside load and save, so two meters can interleave."""

    def __init__(
        self,
        state: LedgerState,
        *,
        conflicts: int = 0,
        fail_loads: int | bool = 0,
        fail_saves: int | bool = 0,
        conflict_hook: Callable[["MemorySpendLedger"], None] | None = None,
        yield_io: bool = False,
    ) -> None:
        self.state = state
        self.revision = 0
        self.conflicts = conflicts
        self.fail_loads = fail_loads
        self.fail_saves = fail_saves
        self.conflict_hook = conflict_hook
        self.yield_io = yield_io
        self.loads = 0
        self.saves = 0  # successful saves
        self.save_attempts = 0

    @staticmethod
    def _should_fail(flag: int | bool) -> tuple[bool, int | bool]:
        if flag is True:
            return True, True
        if flag is False or flag <= 0:
            return False, flag
        return True, flag - 1

    async def load(self) -> tuple[LedgerState, str]:
        if self.yield_io:
            await asyncio.sleep(0)
        fail, self.fail_loads = self._should_fail(self.fail_loads)
        if fail:
            raise LedgerUnavailable("injected load failure")
        self.loads += 1
        return self.state, str(self.revision)

    async def save(self, state: LedgerState, parent: str) -> str:
        if self.yield_io:
            await asyncio.sleep(0)
        self.save_attempts += 1
        fail, self.fail_saves = self._should_fail(self.fail_saves)
        if fail:
            raise LedgerUnavailable("injected save failure")
        if self.conflicts > 0:
            self.conflicts -= 1
            self.revision += 1  # another writer got in first
            if self.conflict_hook is not None:
                self.conflict_hook(self)
            raise LedgerConflict("injected conflict")
        if parent != str(self.revision):
            raise LedgerConflict(f"stale parent {parent}, head {self.revision}")
        self.revision += 1
        self.state = state
        self.saves += 1
        return str(self.revision)
