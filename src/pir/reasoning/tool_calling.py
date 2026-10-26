"""The `tool_calling` reasoning strategy (ARCHITECTURE section 9.1, ADR-0023).

`tools` with `tool_choice="auto"` for up to MAX_TOOL_ROUNDS model calls, then every further call
forces `submit_verdict`. Calls of one turn run in order; `parallel_tool_calls` is never sent and
the temperature is 0. A `submit_verdict` that fails `ModelVerdict` is answered with the validation
error and retried up to MAX_MODEL_RETRIES times, then the outcome is `model_output_invalid`. The
verdict is not trusted here: the gate in `report_builder` decides what reaches a report.
`run` never raises. Prompt and model text are not logged.
"""

import json
import logging
from typing import Any, Literal

from pydantic import ValidationError

from pir.config import Settings, load_settings
from pir.llm import ChatMessage, ChatResult, ForcedTool, LLMClient, LLMError, ToolCall
from pir.models import ReasonCode
from pir.models.reasoning import ModelVerdict, ReasoningOutcome, ToolCallRecord
from pir.reasoning.ast_tool import AstTool, ToolResult
from pir.reasoning.prompts import NO_TOOL_CALL_MESSAGE, RETRY_MESSAGE, reasoning_messages
from pir.reasoning.strategy import FindingContext
from pir.spend import ScanBudget
from pir.tracing import tool_run

logger = logging.getLogger(__name__)

SUBMIT = "submit_verdict"
_MAX_DETAIL_CHARS = 300
_MAX_ARG_TEXT = 200


def _record_arguments(arguments: Any) -> dict[str, str | int | list[str]]:
    """Tool arguments in the shape `ToolCallRecord` allows; anything else becomes JSON text."""
    if not isinstance(arguments, dict):
        return {}
    out: dict[str, str | int | list[str]] = {}
    for key, value in arguments.items():
        if isinstance(value, bool) or not isinstance(value, str | int | list):
            out[str(key)] = json.dumps(value, default=str)[:_MAX_ARG_TEXT]
        elif isinstance(value, list):
            out[str(key)] = (
                list(value)
                if all(isinstance(item, str) for item in value)
                else json.dumps(value, default=str)[:_MAX_ARG_TEXT]
            )
        elif isinstance(value, str):
            out[str(key)] = value[:_MAX_ARG_TEXT]
        else:
            out[str(key)] = value
    return out


def _validation_summary(exc: ValidationError) -> str:
    """Field paths and messages only: the input values are not echoed back."""
    parts = []
    for err in exc.errors(include_input=False, include_url=False)[:5]:
        where = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)[:_MAX_DETAIL_CHARS]


class ToolCallingStrategy:
    name: Literal["tool_calling"] = "tool_calling"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()

    async def run(
        self, ctx: FindingContext, tool: AstTool, llm: LLMClient, budget: ScanBudget
    ) -> ReasoningOutcome:
        state = _Run()
        try:
            return await self._loop(ctx, tool, llm, budget, state)
        except LLMError as exc:
            return state.failed(*_llm_failure(exc))
        except Exception as exc:
            logger.exception("reasoning failed unexpectedly", extra={"service": "reasoning"})
            return state.failed("nebius_failed", f"unexpected error: {type(exc).__name__}")

    async def _loop(
        self, ctx: FindingContext, tool: AstTool, llm: LLMClient, budget: ScanBudget, state: "_Run"
    ) -> ReasoningOutcome:
        settings = self._settings
        messages = reasoning_messages(ctx)
        specs = tool.specs()
        calls_made = 0
        auto_rounds = settings.max_tool_rounds
        while True:
            choice: Literal["auto"] | ForcedTool = (
                "auto" if calls_made < auto_rounds else ForcedTool(name=SUBMIT)
            )
            result = await llm.chat(
                list(messages),
                tools=specs,
                tool_choice=choice,
                max_tokens=settings.max_completion_tokens,
                temperature=0.0,
                timeout_s=settings.timeout_nebius_s,
                budget=budget,
            )
            calls_made += 1
            if not result.tool_calls:
                if isinstance(choice, ForcedTool):
                    state.attempts += 1
                    state.invalid(f"no {SUBMIT} call in the reply")
                    if state.invalid_count > settings.max_model_retries:
                        return state.invalid_outcome()
                else:
                    auto_rounds = calls_made  # a text answer: go straight to the forced round
                messages.append(ChatMessage(role="assistant", content=result.content or ""))
                messages.append(ChatMessage(role="user", content=NO_TOOL_CALL_MESSAGE))
                continue
            messages.append(
                ChatMessage(
                    role="assistant", content=result.content, tool_calls=list(result.tool_calls)
                )
            )
            verdict = self._run_turn(result, tool, messages, state)
            if verdict is not None:
                return state.succeeded(verdict)
            if state.invalid_count > settings.max_model_retries:
                return state.invalid_outcome()

    def _run_turn(
        self, result: ChatResult, tool: AstTool, messages: list[ChatMessage], state: "_Run"
    ) -> ModelVerdict | None:
        """Run the turn's calls in order. Returns the verdict as soon as a valid one arrives."""
        for call in result.tool_calls:
            if call.name == SUBMIT:
                verdict = self._submit(call, messages, state)
                if verdict is not None:
                    return verdict
                continue
            arguments, error = _parse_arguments(call)
            if error is not None:
                answer = ToolResult(False, json.dumps({"error": error}))
            else:
                with tool_run(call.name[:100], _record_arguments(arguments)) as span:
                    answer = tool.call(call.name, arguments)
                    span.set_outputs({"ok": answer.ok, "result_bytes": answer.result_bytes})
            state.tool_calls.append(
                ToolCallRecord(
                    name=call.name[:100],
                    arguments=_record_arguments(arguments),
                    ok=answer.ok,
                    result_bytes=answer.result_bytes,
                )
            )
            messages.append(ChatMessage(role="tool", content=answer.content, tool_call_id=call.id))
        return None

    def _submit(
        self, call: ToolCall, messages: list[ChatMessage], state: "_Run"
    ) -> ModelVerdict | None:
        state.attempts += 1
        arguments, error = _parse_arguments(call)
        verdict: ModelVerdict | None = None
        if error is None:
            try:
                verdict = ModelVerdict.model_validate_json(call.arguments_json)
            except ValidationError as exc:
                error = _validation_summary(exc)
        label = (
            {"verdict": arguments["verdict"]} if isinstance(arguments.get("verdict"), str) else {}
        )
        state.tool_calls.append(
            ToolCallRecord(
                name=SUBMIT,
                arguments=_record_arguments(label),
                ok=verdict is not None,
                result_bytes=len(call.arguments_json.encode("utf-8")),
            )
        )
        if verdict is not None:
            return verdict
        state.invalid(error or "invalid arguments")
        messages.append(
            ChatMessage(
                role="tool",
                content=RETRY_MESSAGE.format(error=state.last_error),
                tool_call_id=call.id,
            )
        )
        return None


def _parse_arguments(call: ToolCall) -> tuple[dict[str, Any], str | None]:
    try:
        arguments = json.loads(call.arguments_json)
    except ValueError:
        return {}, "arguments are not valid JSON"
    if not isinstance(arguments, dict):
        return {}, "arguments must be a JSON object"
    return arguments, None


def _llm_failure(exc: LLMError) -> tuple[ReasonCode, str]:
    if exc.kind == "budget":
        reason: ReasonCode = exc.denied.reason if exc.denied else "scan_budget_reached"
        label = "spend cap reached" if reason == "spend_cap_reached" else "scan budget reached"
        return reason, label
    return "nebius_failed", f"model call failed: {exc.kind}"


class _Run:
    """Mutable record of one `run`, turned into the immutable `ReasoningOutcome`."""

    def __init__(self) -> None:
        self.tool_calls: list[ToolCallRecord] = []
        self.attempts = 0
        self.invalid_count = 0
        self.last_error = ""

    def invalid(self, error: str) -> None:
        self.invalid_count += 1
        self.last_error = error[:_MAX_DETAIL_CHARS]

    def succeeded(self, verdict: ModelVerdict) -> ReasoningOutcome:
        return ReasoningOutcome(
            verdict=verdict,
            tool_calls=self.tool_calls,
            attempts=self.attempts,
            failure=None,
            failure_detail=None,
        )

    def failed(self, failure: ReasonCode, detail: str) -> ReasoningOutcome:
        return ReasoningOutcome(
            verdict=None,
            tool_calls=self.tool_calls,
            attempts=self.attempts,
            failure=failure,
            failure_detail=detail,
        )

    def invalid_outcome(self) -> ReasoningOutcome:
        return self.failed(
            "model_output_invalid",
            f"the model's verdict failed validation {self.invalid_count} times: {self.last_error}",
        )
