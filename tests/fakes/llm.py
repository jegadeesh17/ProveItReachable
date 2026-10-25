"""FakeLLM: an `LLMClient` that returns scripted responses and records every call.

Script entries are used in order (the last repeats): a `ChatResult`, an `LLMError` to raise, a
`str` (shorthand for a text reply) or a callable taking the `FakeLLM.Call`. With `cost_usd` set,
each call holds and records that amount against the budget like the real client.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pir.llm import ChatMessage, ChatResult, ForcedTool, LLMError, ToolCall, ToolSpec, Usage
from pir.spend import ScanBudget, SpendDenied


def usage(prompt: int = 10, completion: int = 5, reasoning: int = 0) -> Usage:
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        estimated=False,
    )


def text_result(content: str, finish_reason: str = "stop") -> ChatResult:
    return ChatResult(content=content, finish_reason=finish_reason, tool_calls=[], usage=usage())


def json_result(payload: Any) -> ChatResult:
    return text_result(json.dumps(payload))


def tool_result(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> ChatResult:
    call = ToolCall(id=call_id, name=name, arguments_json=json.dumps(arguments))
    return ChatResult(content=None, finish_reason="tool_calls", tool_calls=[call], usage=usage())


@dataclass
class Call:
    messages: list[ChatMessage]
    tools: list[ToolSpec] | None
    tool_choice: Literal["auto", "none"] | ForcedTool | None
    json_mode: bool
    max_tokens: int
    temperature: float
    timeout_s: float


Step = ChatResult | LLMError | str | Callable[[Call], ChatResult]


class FakeLLM:
    def __init__(
        self,
        script: list[Step] | None = None,
        *,
        model: str = "nvidia/nemotron-fake",
        cost_usd: float = 0.0,
    ) -> None:
        self.model = model
        self.script: list[Step] = list(script or [])
        self.cost_usd = cost_usd
        self.calls: list[Call] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        tool_choice: Literal["auto", "none"] | ForcedTool | None = None,
        json_mode: bool = False,
        max_tokens: int,
        temperature: float = 0.0,
        timeout_s: float,
        budget: ScanBudget,
    ) -> ChatResult:
        call = Call(messages, tools, tool_choice, json_mode, max_tokens, temperature, timeout_s)
        self.calls.append(call)
        if not self.script:
            raise AssertionError("FakeLLM script is empty")
        step = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        hold = None
        if self.cost_usd:
            decision = budget.try_spend("nebius", self.cost_usd)
            if isinstance(decision, SpendDenied):
                raise LLMError("budget", f"spend denied: {decision.reason}", decision)
            hold = decision
        try:
            if isinstance(step, LLMError):
                raise step
            if isinstance(step, str):
                return text_result(step)
            if callable(step):
                return step(call)
            return step
        finally:
            if hold is not None:
                budget.record(hold, self.cost_usd)
