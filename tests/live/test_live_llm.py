"""Live Token Factory checks (ARCHITECTURE 7.5 and 22.1): run with PIR_LIVE_TESTS=1 (slow + live).

Credentials come from Settings (NEBIUS_API_KEY). Each test makes one small Nemotron call.
"""

import json

import pytest

from pir.config import Settings
from pir.llm import ChatMessage, ForcedTool, NebiusLLMClient, ToolSpec
from pir.spend import ScanBudget

pytestmark = [pytest.mark.slow, pytest.mark.live]

LOOKUP = ToolSpec(
    name="lookup_symbol",
    description="Look up a Python symbol in the code index.",
    parameters={
        "type": "object",
        "properties": {"symbol": {"type": "string", "description": "dotted name"}},
        "required": ["symbol"],
    },
)


def _budget() -> ScanBudget:
    return ScanBudget("live", {"nebius": 0.5, "tavily": 0.0}, {})


async def test_max_completion_tokens_bounds_reasoning():
    """Token Factory must count reasoning tokens inside `max_completion_tokens` (22.1 gate).

    If this fails, set `COMPLETION_LIMIT_PARAM = "max_tokens"` in pir/llm.py (hold falls back to
    LLM_HOLD_COMPLETION_TOKENS) and note it in the QA entry.
    """
    limit = 60
    client = NebiusLLMClient(Settings())
    budget = _budget()
    try:
        result = await client.chat(
            [ChatMessage(role="user", content="Work out 17 * 23 step by step, then answer.")],
            max_tokens=limit,
            timeout_s=60,
            budget=budget,
        )
    finally:
        await client.aclose()
    usage = result.usage
    assert not usage.estimated, "Token Factory returned no usage"
    # reasoning tokens are inside completion_tokens (OpenAI semantics) unless reported larger
    billed = usage.completion_tokens
    if usage.reasoning_tokens > usage.completion_tokens:
        billed += usage.reasoning_tokens
    assert billed <= limit, (
        f"limit {limit} not honoured: completion={usage.completion_tokens} "
        f"reasoning={usage.reasoning_tokens} finish={result.finish_reason}"
    )
    # Not asserted: `budget.overrun`. The chat template adds about 20 prompt tokens that
    # ceil(chars / 2.5) does not cover, so a short prompt that fills the limit can overrun its hold.
    assert budget.usage_usd()["nebius"] > 0


async def test_live_llm_tool_call_and_forced_tool_choice():
    client = NebiusLLMClient(Settings())
    budget = _budget()
    messages = [
        ChatMessage(role="user", content="Is yaml.load in the code index? Use the tool."),
    ]
    try:
        auto = await client.chat(
            messages,
            tools=[LOOKUP],
            tool_choice="auto",
            max_tokens=800,
            timeout_s=60,
            budget=budget,
        )
        forced = await client.chat(
            [ChatMessage(role="user", content="Check the symbol yaml.load.")],
            tools=[LOOKUP],
            tool_choice=ForcedTool(name="lookup_symbol"),
            max_tokens=800,
            timeout_s=60,
            budget=budget,
        )
    finally:
        await client.aclose()
    assert auto.tool_calls, f"no tool call, finish={auto.finish_reason}"
    assert auto.tool_calls[0].name == "lookup_symbol"
    assert "symbol" in json.loads(auto.tool_calls[0].arguments_json)
    assert forced.tool_calls and forced.tool_calls[0].name == "lookup_symbol"
    assert "symbol" in json.loads(forced.tool_calls[0].arguments_json)
