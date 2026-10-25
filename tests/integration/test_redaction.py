"""AC-31 / AC-75 / NFR-03 (security finding 9): no secret value in any log line, report or trace.

Every secret setting is a sentinel. A scan with one failing external call must leak none of them,
including through an ExecutorUnavailable message, an openai exception message and a logged
traceback. Missing required variables are named, never valued.
"""

import httpx
import openai
import pytest
from fakes.deps import (
    ListSink,
    ScriptedExecutor,
    capture_json_logs,
    make_harness,
    write_repo,
)
from fakes.langsmith import FakeLangSmithClient
from fakes.llm import FakeLLM
from langsmith import traceable

from pir.config import ConfigError, Settings, require, required_for
from pir.executor.base import ExecutorUnavailable
from pir.logs import clear_secrets, register_secrets, secrets_from_settings
from pir.pipeline import run_scan
from pir.tracing import make_client, run_traced

SECRETS = {
    "nebius_api_key": "SENTINEL-nebius-3f9a",
    "tavily_api_key": "SENTINEL-tavily-71c2",
    "langsmith_api_key": "SENTINEL-langsmith-9d04",
    "github_token": "SENTINEL-github-55be",
    "hf_token": "SENTINEL-hf-e1a8",
}


@pytest.fixture(autouse=True)
def _clean_secret_registry():
    clear_secrets()
    yield
    clear_secrets()


def _openai_failure(_call):
    raise openai.APIConnectionError(
        message=f"Connection error. Authorization: Bearer {SECRETS['nebius_api_key']}",
        request=httpx.Request("POST", "https://api.tokenfactory.nebius.com/v1/chat/completions"),
    )


def _scenarios():
    """name -> make_harness keyword arguments for that failing call."""
    unavailable = ExecutorUnavailable(f"auth failed for key {SECRETS['nebius_api_key']}")
    return {
        "executor unavailable": {"executor": ScriptedExecutor({"index": unavailable})},
        "openai exception": {"llm": FakeLLM([_openai_failure])},
        "logged traceback": {},
    }


@pytest.mark.parametrize(
    "scenario", ["executor unavailable", "openai exception", "logged traceback"]
)
async def test_ac75_no_sentinel_in_logs_report_events_or_trace(tmp_path, monkeypatch, scenario):
    fake_ls = FakeLangSmithClient()
    kwargs = _scenarios()[scenario]
    h = make_harness(tmp_path, langsmith=fake_ls, secrets=SECRETS, **kwargs)
    if scenario == "logged traceback":

        async def boom(deps):
            raise RuntimeError(f"OSV client crashed with token {SECRETS['github_token']}")

        monkeypatch.setattr(h.deps.osv, "query", boom)
    repo = write_repo(tmp_path / "repo")
    sink = ListSink()
    budget = await h.reserve()

    with capture_json_logs() as logs:
        report = await run_scan(h.deps, h.target(repo), budget, sink=sink)

    outputs = {
        "logs": logs.getvalue(),
        "report": report.model_dump_json(),
        "events": "\n".join(e.model_dump_json() for e in sink.events),
        "trace": fake_ls.text(),
    }
    assert outputs["trace"], "the fake LangSmith client should have received the scan's runs"
    for where, text in outputs.items():
        for name, value in SECRETS.items():
            assert value not in text, f"{name} leaked into the {where}"
    # the failing call really did carry the sentinel into a message that was then redacted
    assert "[REDACTED]" in outputs["logs"] + outputs["report"]


async def test_trace_inputs_and_outputs_are_redacted_by_the_client(tmp_path):
    fake_ls = FakeLangSmithClient()
    settings = Settings(_env_file=None, **SECRETS)
    register_secrets(secrets_from_settings(settings))

    @traceable(name="child", run_type="tool")
    def child(argument: str) -> dict[str, str]:
        return {"echo": argument}

    async def body():
        return child(f"key={SECRETS['tavily_api_key']}")

    result = await run_traced(settings, "e" * 32, body, client=fake_ls)

    assert result == {"echo": f"key={SECRETS['tavily_api_key']}"}  # the caller still gets its data
    assert fake_ls.text() and SECRETS["tavily_api_key"] not in fake_ls.text()
    assert "[REDACTED]" in fake_ls.text()


def test_make_client_is_off_without_the_switch_or_the_key():
    assert make_client(Settings(_env_file=None, langsmith_api_key="k")) is None
    assert make_client(Settings(_env_file=None, langsmith_tracing=True)) is None
    on = make_client(Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key="k"))
    assert on is not None
    on.cleanup()


# ---- AC-31: required variables are named, never valued ------------------------------------


def test_ac31_missing_nebius_key_names_the_variable_only():
    settings = Settings(_env_file=None, tavily_api_key=SECRETS["tavily_api_key"])
    with pytest.raises(ConfigError) as caught:
        require(settings, required_for(settings, "scan"))
    assert "NEBIUS_API_KEY" in str(caught.value)
    assert SECRETS["tavily_api_key"] not in str(caught.value)
    assert "NEBIUS_API_KEY" in caught.value.names
