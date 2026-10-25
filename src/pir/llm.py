"""LLM client (ARCHITECTURE section 7.5): Nemotron on Nebius Token Factory, metered per call.

The base URL is a constant and the HTTP client is the allowlisted one from `pir.http`, so no other
host can be reached (AC-16). `content` is stripped (Nemotron replies start with blank lines). Each
call holds an upper-bound estimate against the scan budget first and records the actual cost
afterwards (ADR-0029). One retry on a timeout or a 5xx. Prompt and model text are never logged.
"""

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

import httpx
import openai

from pir.config import Settings, load_settings, require
from pir.http import MIB, make_client
from pir.models import Strict
from pir.spend import ScanBudget, SpendDenied, SpendHold

logger = logging.getLogger(__name__)

TOKEN_FACTORY_HOST = "api.tokenfactory.nebius.com"
TOKEN_FACTORY_BASE_URL = f"https://{TOKEN_FACTORY_HOST}/v1/"  # constant, not configurable (AC-16)
TOKEN_FACTORY_MAX_BYTES = 5 * MIB

# "max_completion_tokens" bounds visible plus reasoning tokens (OpenAI API). The live check
# `test_max_completion_tokens_bounds_reasoning` verifies Token Factory honours it; if it does not,
# set this to "max_tokens" and the hold becomes LLM_HOLD_COMPLETION_TOKENS (section 7.5).
COMPLETION_LIMIT_PARAM: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"

CHARS_PER_TOKEN = 2.5  # pessimistic prompt-size estimate for the hold
RETRIES = 1
_BACKOFF_S = 0.5

LLMErrorKind = Literal["timeout", "http", "rate_limited", "bad_response", "budget"]
Role = Literal["system", "user", "assistant", "tool"]


class LLMError(Exception):
    """A model call failed. `kind` is one of the section 7.5 kinds; `denied` is set for "budget".

    The message never carries a secret, a prompt or model text.
    """

    def __init__(self, kind: LLMErrorKind, message: str, denied: SpendDenied | None = None) -> None:
        super().__init__(message)
        self.kind: LLMErrorKind = kind
        self.denied = denied


class ToolCall(Strict):
    id: str
    name: str
    arguments_json: str


class Usage(Strict):
    """Token counts. `estimated` is true when the response had no `usage` (the hold was kept)."""

    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    estimated: bool


class ChatMessage(Strict):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None  # assistant messages only
    tool_call_id: str | None = None  # tool messages only


class ToolSpec(Strict):
    """An OpenAI function schema."""

    name: str
    description: str
    parameters: dict[str, Any]


class ForcedTool(Strict):
    name: str


class ChatResult(Strict):
    content: str | None
    finish_reason: str | None
    tool_calls: list[ToolCall]
    usage: Usage


class LLMClient(Protocol):
    model: str

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
    ) -> ChatResult: ...


def _message_dict(message: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments_json},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        out["tool_call_id"] = message.tool_call_id
    return out


def _tool_dict(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def prompt_chars(messages: list[ChatMessage], tools: list[ToolSpec] | None) -> int:
    """Characters the request carries: message text, tool-call text and the tool schemas."""
    total = 0
    for message in messages:
        total += len(message.content or "")
        for call in message.tool_calls or []:
            total += len(call.name) + len(call.arguments_json)
    if tools:
        total += len(json.dumps([_tool_dict(spec) for spec in tools]))
    return total


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _reasoning_tokens(usage: Any) -> int:
    details = getattr(usage, "completion_tokens_details", None)
    value = getattr(details, "reasoning_tokens", None)
    if value is None:
        value = getattr(usage, "reasoning_tokens", None)
    return _count(value)


class NebiusLLMClient:
    """`openai.AsyncOpenAI` over the allowlisted httpx client. Satisfies `LLMClient`."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        completion_limit_param: Literal[
            "max_completion_tokens", "max_tokens"
        ] = COMPLETION_LIMIT_PARAM,
    ) -> None:
        self._settings = settings or load_settings()
        require(self._settings, ["NEBIUS_API_KEY"])
        assert self._settings.nebius_api_key is not None  # require() checked it
        self.model = self._settings.nebius_model
        self._sleep = sleep
        self._limit_param = completion_limit_param
        self._http = make_client(
            {TOKEN_FACTORY_HOST},
            TOKEN_FACTORY_MAX_BYTES,
            self._settings.timeout_nebius_s,
            headers={"User-Agent": "ProveItReachable"},
            transport=transport,
        )
        client = openai.AsyncOpenAI(
            api_key=self._settings.nebius_api_key.get_secret_value(),
            base_url=TOKEN_FACTORY_BASE_URL,
            http_client=self._http,
            max_retries=0,
        )
        if self._settings.langsmith_tracing:
            from langsmith.wrappers import wrap_openai

            client = wrap_openai(client)
        self._openai = client

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- metering ---------------------------------------------------------------------

    def _prices(self) -> tuple[float, float]:
        return (
            self._settings.nebius_price_input_per_mtok / 1_000_000,
            self._settings.nebius_price_output_per_mtok / 1_000_000,
        )

    def _hold_completion_tokens(self, max_tokens: int) -> int:
        if self._limit_param == "max_completion_tokens":
            return max_tokens
        return self._settings.llm_hold_completion_tokens

    def hold_usd(self, chars: int, max_tokens: int) -> float:
        """ceil(chars / 2.5) x input price + completion bound x output price."""
        price_in, price_out = self._prices()
        return (
            math.ceil(chars / CHARS_PER_TOKEN) * price_in
            + self._hold_completion_tokens(max_tokens) * price_out
        )

    def _actual_usd(self, usage: Usage) -> float:
        price_in, price_out = self._prices()
        completion = usage.completion_tokens
        # OpenAI counts reasoning tokens inside completion_tokens. A reasoning count larger than
        # the completion count can only be a separate figure, so it is added.
        if usage.reasoning_tokens > completion:
            completion += usage.reasoning_tokens
        return usage.prompt_tokens * price_in + completion * price_out

    # ---- chat -------------------------------------------------------------------------

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
        if not messages:
            raise ValueError("messages must not be empty")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be a positive number")
        if tool_choice is not None and not tools:
            raise ValueError("tool_choice needs tools")

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_dict(m) for m in messages],
            "temperature": temperature,
            self._limit_param: max_tokens,
        }
        if tools:
            body["tools"] = [_tool_dict(spec) for spec in tools]
            if isinstance(tool_choice, ForcedTool):
                body["tool_choice"] = {"type": "function", "function": {"name": tool_choice.name}}
            elif tool_choice is not None:
                body["tool_choice"] = tool_choice
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        chars = prompt_chars(messages, tools)
        decision = budget.try_spend("nebius", self.hold_usd(chars, max_tokens))
        if isinstance(decision, SpendDenied):
            self._log_end("budget", 0.0, time.monotonic())
            raise LLMError("budget", f"spend denied: {decision.reason}", decision)
        hold: SpendHold = decision

        started = time.monotonic()
        try:
            response = await self._send(body, timeout_s)
        except LLMError as exc:
            # A timeout may still have been billed: keep the hold. Other failures cost nothing.
            budget.record(hold, hold.amount_usd if exc.kind == "timeout" else 0.0)
            self._log_end(exc.kind, hold.amount_usd if exc.kind == "timeout" else 0.0, started)
            raise
        except BaseException:
            budget.record(hold, hold.amount_usd)
            raise

        try:
            result = self._parse(response, chars, hold)
        except LLMError as exc:
            budget.record(hold, hold.amount_usd)
            self._log_end(exc.kind, hold.amount_usd, started)
            raise
        cost = hold.amount_usd if result.usage.estimated else self._actual_usd(result.usage)
        budget.record(hold, cost)
        self._log_end("ok", cost, started)
        return result

    async def _send(self, body: dict[str, Any], timeout_s: float) -> Any:
        last: LLMError | None = None
        for attempt in range(RETRIES + 1):
            if attempt:
                await self._sleep(_BACKOFF_S)
            try:
                async with asyncio.timeout(timeout_s):
                    return await self._openai.chat.completions.create(**body, timeout=timeout_s)
            except (TimeoutError, openai.APITimeoutError):
                last = LLMError("timeout", "Token Factory request timed out")
                continue
            except openai.RateLimitError as exc:
                raise LLMError("rate_limited", "Token Factory returned HTTP 429") from exc
            except openai.APIStatusError as exc:
                last = LLMError("http", f"Token Factory returned HTTP {exc.status_code}")
                if exc.status_code >= 500:
                    continue
                raise last from exc
            except openai.APIConnectionError as exc:
                raise LLMError("http", f"Token Factory request failed: {_cause(exc)}") from exc
            except openai.OpenAIError as exc:
                raise LLMError("bad_response", "Token Factory returned an unusable body") from exc
        assert last is not None
        raise last

    def _parse(self, response: Any, chars: int, hold: SpendHold) -> ChatResult:
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMError("bad_response", "Token Factory returned no choices")
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise LLMError("bad_response", "Token Factory returned no message")
        calls: list[ToolCall] = []
        for call in getattr(message, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            call_id = getattr(call, "id", None)
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            if not (
                isinstance(call_id, str) and isinstance(name, str) and isinstance(arguments, str)
            ):
                raise LLMError("bad_response", "Token Factory returned a malformed tool call")
            calls.append(ToolCall(id=call_id, name=name, arguments_json=arguments))
        content = getattr(message, "content", None)
        if content is not None and not isinstance(content, str):
            raise LLMError("bad_response", "Token Factory returned non-text content")
        raw_usage = getattr(response, "usage", None)
        prompt = getattr(raw_usage, "prompt_tokens", None)
        completion = getattr(raw_usage, "completion_tokens", None)
        if raw_usage is None or not isinstance(prompt, int) or not isinstance(completion, int):
            # No usable usage: the hold stays as the cost (section 7.5).
            price_in, price_out = self._prices()
            estimated_prompt = math.ceil(chars / CHARS_PER_TOKEN)
            remaining = max(0.0, hold.amount_usd - estimated_prompt * price_in)
            usage = Usage(
                prompt_tokens=estimated_prompt,
                completion_tokens=int(remaining / price_out) if price_out > 0 else 0,
                reasoning_tokens=0,
                estimated=True,
            )
        else:
            usage = Usage(
                prompt_tokens=_count(prompt),
                completion_tokens=_count(completion),
                reasoning_tokens=_reasoning_tokens(raw_usage),
                estimated=False,
            )
        finish = getattr(choice, "finish_reason", None)
        return ChatResult(
            content=content.strip() if content is not None else None,
            finish_reason=finish if isinstance(finish, str) else None,
            tool_calls=calls,
            usage=usage,
        )

    def _log_end(self, outcome: str, cost_usd: float, started: float) -> None:
        logger.info(
            "llm call %s",
            outcome,
            extra={
                "event": "call_end",
                "service": "nebius",
                "outcome": outcome,
                "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                "cost_usd": cost_usd,
            },
        )


def _cause(exc: BaseException) -> str:
    cause = exc.__cause__ or exc.__context__
    return type(cause).__name__ if cause is not None else type(exc).__name__
