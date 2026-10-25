"""Dependency wiring (ARCHITECTURE section 7): every client, executor, ledger and meter.

`build_deps(settings)` creates the real components. Tests build `PipelineDeps` by hand from fakes.
"""

import functools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from langsmith import Client

from pir.agent_client import SandboxAgentClient
from pir.config import ConfigError, Settings
from pir.executor.base import Executor
from pir.executor.contree import ContreeExecutor
from pir.executor.fake import FakeExecutor
from pir.github import RepoCoordinates, ValidatedTarget, validate_repo
from pir.llm import LLMClient, NebiusLLMClient
from pir.logs import register_secrets, secrets_from_settings
from pir.osv import OsvClient
from pir.reasoning.strategy import ReasoningStrategy
from pir.reasoning.tool_calling import ToolCallingStrategy
from pir.research import AdvisoryResearcher, TavilySearchClient
from pir.spend import Clock, FileSpendLedger, Spender, SpendMeter, SystemClock
from pir.tracing import flush, make_client

logger = logging.getLogger("pir.deps")

ValidateRepo = Callable[[RepoCoordinates, str | None], Awaitable[ValidatedTarget]]


@dataclass
class PipelineDeps:
    """What a scan needs. `closers` are awaited by `aclose` (HTTP clients and the like)."""

    settings: Settings
    executor: Executor
    agent: SandboxAgentClient
    osv: OsvClient
    researcher: AdvisoryResearcher
    llm: LLMClient
    strategy: ReasoningStrategy
    meter: SpendMeter
    clock: Clock
    validate: ValidateRepo
    langsmith: Client | None = None
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def aclose(self) -> None:
        """Release connections and send queued traces. Never raises."""
        for closer in self.closers:
            try:
                await closer()
            except Exception as exc:  # shutting down: log the type only and carry on
                logger.warning("closing a client failed: %s", type(exc).__name__)
        await flush(self.langsmith)


def build_deps(
    settings: Settings,
    *,
    spender: Spender = "local",
    langsmith: Client | None = None,
    clock: Clock | None = None,
) -> PipelineDeps:
    """Create the real components for `settings`. `settings.executor` picks the executor.

    Raises ConfigError for a missing key and for a ledger backend this build does not have.
    """
    register_secrets(secrets_from_settings(settings))
    clock = clock or SystemClock()
    if settings.spend_ledger_backend != "file":
        raise ConfigError(
            "SPEND_LEDGER_BACKEND=hf_dataset is not available in this build; use the file ledger"
        )
    ledger = FileSpendLedger.from_settings(settings, clock)
    meter = SpendMeter.from_settings(ledger, spender, settings, clock)
    executor: Executor
    if settings.executor == "fake":
        executor = FakeExecutor()
    else:
        executor = ContreeExecutor(settings)
    llm = NebiusLLMClient(settings)
    search = TavilySearchClient(settings)
    deps = PipelineDeps(
        settings=settings,
        executor=executor,
        agent=SandboxAgentClient(executor, settings),
        osv=OsvClient(settings),
        researcher=AdvisoryResearcher(settings, search=search, llm=llm),
        llm=llm,
        strategy=ToolCallingStrategy(settings),
        meter=meter,
        clock=clock,
        validate=functools.partial(validate_repo, settings=settings),
        langsmith=langsmith if langsmith is not None else make_client(settings),
    )
    deps.closers.append(llm.aclose)
    return deps


__all__ = ["PipelineDeps", "ValidateRepo", "build_deps"]
