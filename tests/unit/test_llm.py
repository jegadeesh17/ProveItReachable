"""Nebius LLM client (AC-16, AC-25 client part, NFR-04): allowlist, strip, hold and metering."""

import json
import logging
import math

import httpx
import pytest
from fakes.http import RecordingTransport, json_response
from pydantic import ValidationError

from pir.config import ConfigError, Settings
from pir.http import OutboundBlocked
from pir.llm import (
    PROMPT_OVERHEAD_TOKENS,
    TOKEN_FACTORY_BASE_URL,
    ChatMessage,
    ForcedTool,
    LLMError,
    NebiusLLMClient,
    ToolCall,
    ToolSpec,
    prompt_chars,
)
from pir.spend import ScanBudget, SpendDenied

URL = "POST https://api.tokenfactory.nebius.com/v1/chat/completions"
KEY = "sentinel-nebius-key"
IN_PRICE = 0.60 / 1_000_000
OUT_PRICE = 2.40 / 1_000_000
HELLO = [ChatMessage(role="user", content="x" * 100)]


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, nebius_api_key=KEY, **kw)


def _body(content="\n\nhello\n", usage="default", tool_calls=None, finish="stop") -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body: dict = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage == "default":
        usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    if usage is not None:
        body["usage"] = usage
    return body


def _budget(nebius: float = 1.0) -> ScanBudget:
    return ScanBudget("scan", {"nebius": nebius, "tavily": 0.0}, {})


class NoSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _client(routes, **kw) -> tuple[NebiusLLMClient, RecordingTransport, NoSleep]:
    transport = RecordingTransport(routes)
    sleep = NoSleep()
    settings = kw.pop("settings", None) or _settings()
    return NebiusLLMClient(settings, transport=transport, sleep=sleep, **kw), transport, sleep


async def _chat(client, budget=None, **kw):
    kw.setdefault("max_tokens", 1500)
    kw.setdefault("timeout_s", 30.0)
    return await client.chat(HELLO, budget=budget or _budget(), **kw)


# ---- AC-16 / NFR-04 ------------------------------------------------------------------


def test_ac16_base_url_is_a_constant():
    assert TOKEN_FACTORY_BASE_URL == "https://api.tokenfactory.nebius.com/v1/"
    assert not [name for name in Settings.model_fields if "base_url" in name]
    client, _, _ = _client({})
    assert str(client._openai.base_url) == TOKEN_FACTORY_BASE_URL


async def test_ac16_http_client_refuses_any_other_host():
    client, transport, _ = _client({})
    for url in (
        "https://api.openai.com/v1/chat/completions",
        "https://api.tokenfactory.nebius.com.evil.test/v1/",
        "http://api.tokenfactory.nebius.com/v1/",
        "https://api.tokenfactory.nebius.com:8443/v1/",
    ):
        with pytest.raises(OutboundBlocked):
            await client._http.post(url, json={})
    assert transport.requests == []


def test_nfr04_settings_refuse_a_non_nemotron_model():
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, nebius_model="gpt-4o")
    assert "NEBIUS_MODEL" in str(caught.value)
    assert Settings(_env_file=None, nebius_model="NVIDIA/NEMOTRON-x").nebius_model


async def test_ac16_request_goes_to_token_factory_with_bearer_and_model():
    client, transport, _ = _client({URL: json_response(200, _body())})
    await _chat(client)
    (request,) = transport.requests
    assert str(request.url) == "https://api.tokenfactory.nebius.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert client.model == body["model"]


def test_missing_api_key_names_only_the_variable():
    with pytest.raises(ConfigError) as caught:
        NebiusLLMClient(Settings(_env_file=None, nebius_api_key=None))
    assert "NEBIUS_API_KEY" in str(caught.value)


# ---- request shape -------------------------------------------------------------------


async def test_content_with_leading_blank_lines_is_stripped():
    client, _, _ = _client({URL: json_response(200, _body(content='\n\n  {"a": 1}\n '))})
    result = await _chat(client, json_mode=True)
    assert result.content == '{"a": 1}'
    assert json.loads(result.content) == {"a": 1}
    assert result.finish_reason == "stop"


async def test_json_mode_temperature_and_completion_limit_in_body():
    client, transport, _ = _client({URL: json_response(200, _body())})
    await _chat(client, json_mode=True, max_tokens=321)
    body = transport.json_bodies()[0]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.0
    assert body["max_completion_tokens"] == 321
    assert "max_tokens" not in body
    assert "tools" not in body and "tool_choice" not in body
    assert "parallel_tool_calls" not in body


async def test_plain_call_has_no_response_format():
    client, transport, _ = _client({URL: json_response(200, _body())})
    await _chat(client)
    assert "response_format" not in transport.json_bodies()[0]


SPEC = ToolSpec(
    name="lookup",
    description="look a symbol up",
    parameters={"type": "object", "properties": {"symbol": {"type": "string"}}},
)


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("auto", "auto"),
        ("none", "none"),
        (ForcedTool(name="lookup"), {"type": "function", "function": {"name": "lookup"}}),
    ],
)
async def test_tool_choice_modes_are_sent(choice, expected):
    client, transport, _ = _client({URL: json_response(200, _body())})
    await _chat(client, tools=[SPEC], tool_choice=choice)
    body = transport.json_bodies()[0]
    assert body["tool_choice"] == expected
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "look a symbol up",
                "parameters": SPEC.parameters,
            },
        }
    ]
    assert "parallel_tool_calls" not in body


async def test_tools_without_choice_leave_choice_unset():
    client, transport, _ = _client({URL: json_response(200, _body())})
    await _chat(client, tools=[SPEC])
    assert "tool_choice" not in transport.json_bodies()[0]


async def test_tool_calls_and_tool_messages_round_trip():
    calls = [
        {
            "id": "call_9",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"symbol": "yaml.load"}'},
        }
    ]
    client, transport, _ = _client(
        {URL: json_response(200, _body(content=None, tool_calls=calls, finish="tool_calls"))}
    )
    history = [
        ChatMessage(role="user", content="go"),
        ChatMessage(
            role="assistant",
            tool_calls=[ToolCall(id="call_8", name="lookup", arguments_json="{}")],
        ),
        ChatMessage(role="tool", content="result", tool_call_id="call_8"),
    ]
    result = await client.chat(
        history, tools=[SPEC], tool_choice="auto", max_tokens=100, timeout_s=5, budget=_budget()
    )
    assert result.content is None and result.finish_reason == "tool_calls"
    (call,) = result.tool_calls
    assert (call.id, call.name, call.arguments_json) == (
        "call_9",
        "lookup",
        '{"symbol": "yaml.load"}',
    )
    sent = transport.json_bodies()[0]["messages"]
    assert sent[1]["tool_calls"][0] == {
        "id": "call_8",
        "type": "function",
        "function": {"name": "lookup", "arguments": "{}"},
    }
    assert sent[2] == {"role": "tool", "content": "result", "tool_call_id": "call_8"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tokens": 0},
        {"max_tokens": -5},
        {"timeout_s": 0},
        {"timeout_s": float("nan")},
        {"tool_choice": "auto"},
    ],
)
async def test_invalid_arguments_are_refused_before_any_request(kwargs):
    client, transport, _ = _client({})
    with pytest.raises(ValueError):
        await _chat(client, **kwargs)
    assert transport.requests == []


async def test_empty_messages_are_refused():
    client, transport, _ = _client({})
    with pytest.raises(ValueError):
        await client.chat([], max_tokens=10, timeout_s=5, budget=_budget())
    assert transport.requests == []


# ---- hold and metering ---------------------------------------------------------------


def _expected_hold(chars: int, completion_tokens: int) -> float:
    return (math.ceil(chars / 2.5) + 64) * IN_PRICE + completion_tokens * OUT_PRICE


async def test_hold_is_ceil_chars_over_2_5_plus_64_times_input_plus_max_tokens_times_output():
    budget = _budget()
    seen: list[float] = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(budget.usage_usd()["nebius"])
        return json_response(200, _body())

    client, _, _ = _client({URL: route})
    await _chat(client, budget, max_tokens=1500)
    assert prompt_chars(HELLO, None) == 100
    assert seen == [pytest.approx(_expected_hold(100, 1500))]


async def test_hold_counts_tool_schema_characters():
    budget = _budget()
    seen: list[float] = []

    def route(request):
        seen.append(budget.usage_usd()["nebius"])
        return json_response(200, _body())

    client, _, _ = _client({URL: route})
    await _chat(client, budget, tools=[SPEC], max_tokens=100)
    chars = prompt_chars(HELLO, [SPEC])
    assert chars > 100
    assert seen == [pytest.approx(_expected_hold(chars, 100))]


async def test_actual_cost_replaces_the_hold():
    budget = _budget()
    client, _, _ = _client({URL: json_response(200, _body())})
    result = await _chat(client, budget)
    assert result.usage.prompt_tokens == 100 and result.usage.completion_tokens == 20
    assert not result.usage.estimated
    assert budget.usage_usd()["nebius"] == pytest.approx(100 * IN_PRICE + 20 * OUT_PRICE)
    assert not budget.overrun


async def test_reasoning_tokens_inside_completion_are_not_double_counted():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "completion_tokens_details": {"reasoning_tokens": 30},
    }
    budget = _budget()
    client, _, _ = _client({URL: json_response(200, _body(usage=usage))})
    result = await _chat(client, budget)
    assert result.usage.reasoning_tokens == 30
    assert budget.usage_usd()["nebius"] == pytest.approx(100 * IN_PRICE + 50 * OUT_PRICE)


async def test_separately_reported_reasoning_tokens_are_added():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "completion_tokens_details": {"reasoning_tokens": 300},
    }
    budget = _budget()
    client, _, _ = _client({URL: json_response(200, _body(usage=usage))})
    await _chat(client, budget)
    assert budget.usage_usd()["nebius"] == pytest.approx(100 * IN_PRICE + 310 * OUT_PRICE)


async def test_missing_usage_keeps_the_hold():
    budget = _budget()
    client, _, _ = _client({URL: json_response(200, _body(usage=None))})
    result = await _chat(client, budget, max_tokens=1500)
    assert result.usage.estimated
    assert budget.usage_usd()["nebius"] == pytest.approx(_expected_hold(100, 1500))


async def test_adr0035_short_prompt_filling_its_limit_does_not_overrun_the_hold():
    # Live finding: a 43-character prompt was billed 32 prompt tokens; ceil(43 / 2.5) is 18.
    usage = {"prompt_tokens": 32, "completion_tokens": 200}
    budget = _budget()
    client, _, _ = _client({URL: json_response(200, _body(usage=usage, finish="length"))})
    short = [ChatMessage(role="user", content="x" * 43)]
    await client.chat(short, max_tokens=200, timeout_s=30.0, budget=budget)
    assert PROMPT_OVERHEAD_TOKENS == 64
    assert not budget.overrun
    assert budget.usage_usd()["nebius"] == pytest.approx(32 * IN_PRICE + 200 * OUT_PRICE)
    assert budget.usage_usd()["nebius"] <= _expected_hold(43, 200)


async def test_actual_over_hold_marks_the_scan_overrun_and_denies_later_spend():
    usage = {"prompt_tokens": 100, "completion_tokens": 9000}
    budget = _budget()
    client, _, _ = _client({URL: json_response(200, _body(usage=usage))})
    await _chat(client, budget, max_tokens=100)
    assert budget.overrun
    assert budget.usage_usd()["nebius"] == pytest.approx(100 * IN_PRICE + 9000 * OUT_PRICE)
    with pytest.raises(LLMError) as caught:
        await _chat(client, budget)
    assert caught.value.kind == "budget"
    assert caught.value.denied == SpendDenied("nebius", "scan_budget_reached")


async def test_fallback_mode_sends_max_tokens_and_holds_the_worst_case():
    budget = _budget()
    seen: list[float] = []

    def route(request):
        seen.append(budget.usage_usd()["nebius"])
        return json_response(200, _body())

    client, transport, _ = _client({URL: route}, completion_limit_param="max_tokens")
    await _chat(client, budget, max_tokens=50)
    body = transport.json_bodies()[0]
    assert body["max_tokens"] == 50 and "max_completion_tokens" not in body
    assert seen == [pytest.approx(_expected_hold(100, 16000))]


async def test_budget_denied_sends_nothing():
    client, transport, _ = _client({})
    with pytest.raises(LLMError) as caught:
        await _chat(client, _budget(nebius=0.0001), max_tokens=1500)
    assert caught.value.kind == "budget"
    assert caught.value.denied == SpendDenied("nebius", "scan_budget_reached")
    assert transport.requests == []


async def test_budget_denied_reports_the_spend_cap_reason():
    budget = ScanBudget("scan", {"nebius": 0.0, "tavily": 0.0}, {"nebius": "spend_cap_reached"})
    client, _, _ = _client({})
    with pytest.raises(LLMError) as caught:
        await _chat(client, budget)
    assert caught.value.denied == SpendDenied("nebius", "spend_cap_reached")


# ---- failures (AC-25 client part) ----------------------------------------------------


async def test_ac25_5xx_is_retried_once_then_succeeds():
    client, transport, sleep = _client(
        {URL: [json_response(503, {"error": "busy"}), json_response(200, _body())]}
    )
    result = await _chat(client)
    assert result.content == "hello"
    assert len(transport.requests) == 2 and len(sleep.calls) == 1


async def test_ac25_5xx_twice_raises_http_error_and_releases_the_hold():
    budget = _budget()
    client, transport, _ = _client({URL: json_response(502, {"error": "bad gateway"})})
    with pytest.raises(LLMError) as caught:
        await _chat(client, budget)
    assert caught.value.kind == "http" and "502" in str(caught.value)
    assert len(transport.requests) == 2
    assert budget.usage_usd()["nebius"] == 0.0


async def test_ac25_timeout_is_retried_once_then_raises_and_keeps_the_hold():
    budget = _budget()
    client, transport, _ = _client({URL: httpx.ReadTimeout("slow")})
    with pytest.raises(LLMError) as caught:
        await _chat(client, budget, max_tokens=1500)
    assert caught.value.kind == "timeout"
    assert len(transport.requests) == 2
    assert budget.usage_usd()["nebius"] == pytest.approx(_expected_hold(100, 1500))


async def test_ac25_timeout_then_success_records_only_the_actual():
    budget = _budget()
    client, _, _ = _client({URL: [httpx.ReadTimeout("slow"), json_response(200, _body())]})
    await _chat(client, budget)
    assert budget.usage_usd()["nebius"] == pytest.approx(100 * IN_PRICE + 20 * OUT_PRICE)


@pytest.mark.parametrize(
    ("status", "kind"), [(400, "http"), (401, "http"), (404, "http"), (429, "rate_limited")]
)
async def test_client_errors_are_not_retried(status, kind):
    client, transport, _ = _client({URL: json_response(status, {"error": {"message": "no"}})})
    with pytest.raises(LLMError) as caught:
        await _chat(client)
    assert caught.value.kind == kind
    assert len(transport.requests) == 1


async def test_empty_choices_is_a_bad_response_and_keeps_the_hold():
    body = {"id": "x", "object": "chat.completion", "created": 1, "model": "m", "choices": []}
    budget = _budget()
    client, _, _ = _client({URL: json_response(200, body)})
    with pytest.raises(LLMError) as caught:
        await _chat(client, budget)
    assert caught.value.kind == "bad_response"
    assert budget.usage_usd()["nebius"] > 0  # status 200: it may have been billed


async def test_malformed_tool_call_is_a_bad_response():
    calls = [{"id": "c", "type": "function", "function": {"name": "lookup"}}]
    client, _, _ = _client(
        {URL: json_response(200, _body(content=None, tool_calls=calls, finish="tool_calls"))}
    )
    with pytest.raises(LLMError) as caught:
        await _chat(client)
    assert caught.value.kind == "bad_response"


async def test_error_messages_carry_no_secret_and_no_prompt_text():
    client, _, _ = _client({URL: json_response(500, {"error": f"echo {KEY}"})})
    with pytest.raises(LLMError) as caught:
        await _chat(client)
    assert KEY not in str(caught.value) and "x" * 20 not in str(caught.value)


async def test_call_end_log_line_has_outcome_and_no_text(caplog):
    client, _, _ = _client({URL: json_response(200, _body(content="SECRET-MODEL-TEXT"))})
    with caplog.at_level(logging.INFO, logger="pir.llm"):
        await _chat(client)
    (record,) = [r for r in caplog.records if getattr(r, "event", "") == "call_end"]
    assert record.service == "nebius" and record.outcome == "ok"
    assert record.cost_usd == pytest.approx(100 * IN_PRICE + 20 * OUT_PRICE)
    assert record.duration_ms >= 0
    assert "SECRET-MODEL-TEXT" not in record.getMessage()
