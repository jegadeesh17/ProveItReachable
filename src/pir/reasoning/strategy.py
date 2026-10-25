"""The reasoning strategy interface (ARCHITECTURE section 7.6, ADR-0023).

Only `tool_calling` exists. The interface stays so a JSON-mode loop could be added without
touching callers.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from pir.llm import LLMClient
from pir.models.reasoning import ReasoningOutcome
from pir.models.report import Advisory
from pir.reasoning.ast_tool import AstTool
from pir.spend import ScanBudget


@dataclass(frozen=True)
class FindingContext:
    """What the model is told about one finding. `direct` is None when unknown."""

    package: str
    version: str
    advisory: Advisory
    symbols: list[str]  # grounded, dotted
    target_modules: list[str]  # top-level import names of the package, from the index
    direct: bool | None


class ReasoningStrategy(Protocol):
    name: Literal["tool_calling"]

    async def run(
        self, ctx: FindingContext, tool: AstTool, llm: LLMClient, budget: ScanBudget
    ) -> ReasoningOutcome:
        """Never raises: every failure is in `ReasoningOutcome.failure`."""
        ...
