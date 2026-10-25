"""Executor protocol and result types (ARCHITECTURE section 7.1)."""

from typing import Literal, Protocol

from pir.models import Strict
from pir.spend import ScanBudget, SpendDenied


class ImageRef(Strict):
    """An image the executor can run from. sandbox: image UUID; fake: a temp directory."""

    kind: Literal["sandbox", "fake"]
    ref: str


class ExecResult(Strict):
    """One command's outcome.

    `timed_out`: the platform's `state.timed_out` or the host deadline (exit code -1).
    `truncated`: output was cut at the executor's output limit.
    `cost_usd`: what was metered (section 11). `image`: set iff `save=True` and not `timed_out`.
    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_s: float
    cost_usd: float
    image: ImageRef | None


class ExecutorUnavailable(Exception):
    """Auth, HTTP error, status FAILED or CANCELLED, or a malformed body. Never carries a secret."""


class ExecutorSpendDenied(Exception):
    """The scan budget refused the run's hold (`SpendDenied` is a value, so it is wrapped here).

    Raised before any request is sent. `denied.reason` is `spend_cap_reached` or
    `scan_budget_reached`.
    """

    def __init__(self, denied: SpendDenied) -> None:
        super().__init__(f"spend denied for pool {denied.pool}: {denied.reason}")
        self.denied = denied


class Executor(Protocol):
    kind: Literal["sandbox", "fake"]

    async def prepare(self, files: dict[str, bytes]) -> ImageRef:
        """Base image plus trusted files (absolute image paths -> bytes)."""
        ...

    async def run(
        self,
        image: ImageRef,
        argv: list[str],
        *,
        network: bool,
        timeout_s: float,
        save: bool = False,
        budget: ScanBudget | None = None,
    ) -> ExecResult:
        """Run `argv` from `image`. Raises ExecutorUnavailable or ExecutorSpendDenied; a timeout is
        a result."""
        ...

    async def discard(self, image: ImageRef) -> None:
        """Best effort; never raises."""
        ...
