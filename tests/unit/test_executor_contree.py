"""ContreeExecutor with the fake SDK and recorded REST (AC-27, AC-30, NFR-02 executor part).

Security carry-over: uid/gid 65534, networking per call, the fixed `env -i` environment, SDK
`run()` only in `prepare`, reported cost on the budget, SpendDenied before any request.
"""

import asyncio
import json
import logging
import shlex

import httpx
import pytest
from fakes.contree import API_KEY, PROJECT, FakeContree
from fakes.http import RecordingTransport, fixture_json, json_response

from pir.config import Settings
from pir.executor.base import ExecResult, ExecutorSpendDenied, ExecutorUnavailable, ImageRef
from pir.executor.contree import (
    CANCEL_GRACE_S,
    ENV_PREFIX,
    PREPARE_CMD,
    SANDBOX_MAX_OUTPUT_BYTES,
    ContreeExecutor,
    build_command,
    files_key,
    split_base_image,
)
from pir.sandbox_agent.resolve import UV_ENV
from pir.spend import ScanBudget

BASE = "https://api.tokenfactory.nebius.com/sandboxes"
POST = f"POST {BASE}/v1/instances"
IMAGE = ImageRef(kind="sandbox", ref="c6688282-43ba-472c-b15e-2383d2b99ad0")
ARGV = [
    "/usr/local/bin/python3.13",
    "-I",
    "-S",
    "/opt/pir/agent.pyz",
    "index",
    "--args-b64",
    "e30=",
]
RATE = 0.001


def created() -> dict:
    return fixture_json("contree", "instance_created.json")


def op_url(name: str = "instance_created.json") -> str:
    return f"GET {BASE}/v1/operations/{fixture_json('contree', name)['uuid']}"


def op(name: str) -> httpx.Response:
    return json_response(200, fixture_json("contree", name))


def routes(*ops: str, extra: dict | None = None) -> dict:
    table: dict = {POST: json_response(201, created()), op_url(): [op(n) for n in ops]}
    table.update(extra or {})
    return table


async def no_sleep(_: float) -> None:
    return None


def make(
    transport: RecordingTransport | None = None,
    sdk: FakeContree | None = None,
    *,
    ops: int = 4,
    sleep=no_sleep,
    clock=None,
) -> tuple[ContreeExecutor, FakeContree, RecordingTransport]:
    sdk = sdk or FakeContree()
    transport = transport or RecordingTransport(routes("op_success.json"))
    settings = Settings(_env_file=None, sandbox_max_concurrent_ops=ops, sandbox_usd_per_second=RATE)
    kwargs = {"clock": clock} if clock is not None else {}
    executor = ContreeExecutor(settings, client=sdk, transport=transport, sleep=sleep, **kwargs)
    return executor, sdk, transport


def budget(nebius: float = 1.0) -> ScanBudget:
    return ScanBudget("scan", {"nebius": nebius, "tavily": 0.0}, {})


def post_body(transport: RecordingTransport) -> dict:
    posts = [r for r in transport.requests if r.method == "POST"]
    assert len(posts) == 1
    return json.loads(posts[0].content)


# ---- the REST request ---------------------------------------------------------------------


async def test_ac30_one_rest_instance_request_carries_the_security_fields():
    executor, sdk, transport = make()
    result = await executor.run(IMAGE, ARGV, network=False, timeout_s=119.2)
    posts = [r for r in transport.requests if r.method == "POST"]
    assert [str(r.url) for r in posts] == [f"{BASE}/v1/instances"]
    body = post_body(transport)
    assert (body["uid"], body["gid"]) == (65534, 65534)
    assert body["networking"] == {"enabled": False}
    assert body["image"] == IMAGE.ref
    assert body["timeout"] == 120  # ceil
    assert body["truncate_output_at"] == SANDBOX_MAX_OUTPUT_BYTES == 10 * 1024 * 1024
    assert body["disposable"] is True
    assert body["shell"] is True and body["env"] == {} and body["args"] == []
    assert isinstance(result, ExecResult) and result.exit_code == 0
    assert sdk.run_calls == []  # the SDK run() is never used for a command


@pytest.mark.parametrize("network", [True, False])
async def test_networking_enabled_is_exactly_the_argument(network):
    executor, _, transport = make()
    await executor.run(IMAGE, ARGV, network=network, timeout_s=10)
    assert post_body(transport)["networking"] == {"enabled": network}


@pytest.mark.parametrize("save", [True, False])
async def test_disposable_is_the_inverse_of_save(save):
    name = "op_saved.json" if save else "op_success.json"
    executor, _, transport = make(RecordingTransport(routes(name)))
    await executor.run(IMAGE, ARGV, network=False, timeout_s=10, save=save)
    assert post_body(transport)["disposable"] is (not save)


async def test_command_is_the_fixed_env_prefix_plus_the_quoted_argv():
    executor, _, transport = make()
    argv = ["/bin/echo", "a b", "$(touch x)", "it's"]
    await executor.run(IMAGE, argv, network=False, timeout_s=10)
    command = post_body(transport)["command"]
    assert command == f"{ENV_PREFIX} {shlex.join(argv)}" == build_command(argv)
    tokens = shlex.split(command)
    assert tokens[:2] == ["env", "-i"]
    pairs = tokens[2 : 2 + len(UV_ENV)]
    assert dict(p.split("=", 1) for p in pairs) == UV_ENV  # nothing else is set
    assert tokens[2 + len(UV_ENV) :] == argv  # the argv survives shell quoting unchanged
    assert "UV_NO_BUILD=1" in pairs and "UV_PYTHON_DOWNLOADS=never" in pairs
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "HTTP_PROXY", "NEBIUS_API_KEY"):
        assert name not in command
    assert API_KEY not in command


async def test_auth_headers_come_from_the_sdk_auth_and_the_body_has_no_secret():
    executor, _, transport = make()
    await executor.run(IMAGE, ARGV, network=False, timeout_s=10)
    request = transport.requests[0]
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["project"] == PROJECT
    assert API_KEY.encode() not in request.content


# ---- result mapping -----------------------------------------------------------------------


async def test_ac27_success_with_timed_out_state_is_a_timeout_not_a_success():
    executor, _, _ = make(RecordingTransport(routes("op_timed_out.json")))
    result = await executor.run(IMAGE, ARGV, network=False, timeout_s=1, save=True)
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.image is None  # image only when save and not timed out


async def test_success_maps_exit_code_streams_and_truncation():
    executor, _, _ = make(RecordingTransport(routes("op_exit_nonzero.json")))
    result = await executor.run(IMAGE, ARGV, network=False, timeout_s=10)
    assert (result.exit_code, result.stderr, result.timed_out, result.truncated) == (
        3,
        "boom\n",
        False,
        False,
    )
    assert result.image is None


async def test_ac27_truncated_output_is_flagged():
    executor, _, _ = make(RecordingTransport(routes("op_truncated.json")))
    result = await executor.run(IMAGE, ARGV, network=False, timeout_s=10)
    assert result.truncated is True
    assert len(result.stdout) == 1000


async def test_saved_run_returns_the_result_image():
    executor, _, _ = make(RecordingTransport(routes("op_saved.json")))
    result = await executor.run(IMAGE, ARGV, network=True, timeout_s=10, save=True)
    assert result.image == ImageRef(kind="sandbox", ref="1cd3e740-bbc4-42d6-957d-4da1fc3d328f")
    assert result.stdout == "saved\n"


async def test_saved_run_without_an_image_is_unavailable():
    data = fixture_json("contree", "op_saved.json")
    data["result_image_uuid"] = None
    executor, _, _ = make(RecordingTransport(routes(extra={op_url(): json_response(200, data)})))
    with pytest.raises(ExecutorUnavailable, match="no result image"):
        await executor.run(IMAGE, ARGV, network=True, timeout_s=10, save=True)


@pytest.mark.parametrize("name", ["op_failed.json", "op_cancelled.json"])
async def test_failed_and_cancelled_statuses_are_unavailable(name):
    executor, _, _ = make(RecordingTransport(routes(name)))
    with pytest.raises(ExecutorUnavailable, match=name.split("_")[1].split(".")[0].upper()):
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["metadata"].update(result=None),
        lambda d: d["metadata"]["result"]["state"].update(timed_out="no"),
        lambda d: d["metadata"]["result"]["state"].update(exit_code="0"),
        lambda d: d["metadata"]["result"]["stdout"].update(encoding="rot13"),
        lambda d: d["metadata"]["result"]["stdout"].update(value=7),
        lambda d: d.update(status="WEIRD"),
    ],
)
async def test_malformed_operation_bodies_are_unavailable(mutate):
    data = fixture_json("contree", "op_success.json")
    mutate(data)
    transport = RecordingTransport(routes(extra={op_url(): json_response(200, data)}))
    executor, _, _ = make(transport)
    with pytest.raises(ExecutorUnavailable):
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10)


async def test_base64_output_is_decoded():
    data = fixture_json("contree", "op_success.json")
    data["metadata"]["result"]["stdout"].update(encoding="base64", value="aGVsbG8K")
    transport = RecordingTransport(routes(extra={op_url(): json_response(200, data)}))
    executor, _, _ = make(transport)
    result = await executor.run(IMAGE, ARGV, network=False, timeout_s=10)
    assert result.stdout == "hello\n"


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_is_unavailable_and_never_echoes_the_key(status):
    transport = RecordingTransport({POST: httpx.Response(status, json={"error": API_KEY})})
    executor, _, _ = make(transport)
    with pytest.raises(ExecutorUnavailable) as caught:
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10)
    assert API_KEY not in str(caught.value) and str(status) in str(caught.value)


async def test_http_error_and_non_json_body_are_unavailable():
    executor, _, _ = make(RecordingTransport({POST: httpx.Response(500, text="boom")}))
    with pytest.raises(ExecutorUnavailable, match="HTTP 500"):
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10)
    executor, _, _ = make(RecordingTransport({POST: httpx.Response(201, text="<html>")}))
    with pytest.raises(ExecutorUnavailable, match="not JSON"):
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10)


async def test_transport_error_is_unavailable_without_details():
    transport = RecordingTransport({POST: httpx.ConnectError(f"cannot reach {API_KEY}")})
    executor, _, _ = make(transport)
    with pytest.raises(ExecutorUnavailable) as caught:
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10)
    assert API_KEY not in str(caught.value)


async def test_the_platform_400_for_an_oversized_truncate_limit_is_unavailable():
    recorded = fixture_json("contree", "error_truncate_too_big.json")
    transport = RecordingTransport(
        {POST: httpx.Response(recorded["status_code"], json=recorded["body"])}
    )
    executor, _, _ = make(transport)
    with pytest.raises(ExecutorUnavailable, match="HTTP 400"):
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10)


async def test_non_https_sdk_url_is_refused_before_any_request():
    sdk = FakeContree()
    sdk.config.auth.base_url = "http://api.tokenfactory.nebius.com/sandboxes/"
    executor, _, transport = make(sdk=sdk)
    with pytest.raises(ExecutorUnavailable, match="https"):
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10)
    assert transport.requests == []


@pytest.mark.parametrize(
    ("argv", "timeout_s"),
    [([], 10), ([""], 10), (["a"], 0), (["a"], -1), (["a"], float("nan")), (["a"], float("inf"))],
)
async def test_invalid_arguments_are_rejected_before_any_request(argv, timeout_s):
    executor, _, transport = make()
    with pytest.raises(ValueError):
        await executor.run(IMAGE, argv, network=False, timeout_s=timeout_s)
    assert transport.requests == []


async def test_a_fake_image_is_refused():
    executor, _, _ = make()
    with pytest.raises(ValueError, match="sandbox images"):
        await executor.run(ImageRef(kind="fake", ref="/tmp/x"), ARGV, network=False, timeout_s=1)


# ---- polling, deadline and concurrency ----------------------------------------------------


async def test_polling_starts_at_half_a_second_and_doubles_to_two():
    sleeps: list[float] = []

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    names = ["op_pending.json"] * 5 + ["op_success.json"]
    executor, _, transport = make(RecordingTransport(routes(*names)), sleep=record)
    await executor.run(IMAGE, ARGV, network=False, timeout_s=60)
    assert sleeps == [0.5, 1.0, 2.0, 2.0, 2.0]
    assert [r.method for r in transport.requests] == ["POST"] + ["GET"] * 6


async def test_operation_not_terminal_at_timeout_plus_30_is_cancelled_and_a_timeout():
    now = [0.0]

    async def advance(seconds: float) -> None:
        now[0] += seconds

    delete = f"DELETE {BASE}/v1/operations/{created()['uuid']}"
    transport = RecordingTransport(routes("op_pending.json", extra={delete: httpx.Response(204)}))
    bud = budget()
    executor, _, _ = make(transport, sleep=advance, clock=lambda: now[0])
    result = await executor.run(IMAGE, ARGV, network=False, timeout_s=10, save=True, budget=bud)
    assert result.timed_out is True and result.exit_code == -1 and result.image is None
    assert result.duration_s >= 10 + CANCEL_GRACE_S
    assert transport.urls[-1] == delete
    assert bud.usage_usd()["nebius"] == pytest.approx(10 * RATE)  # the hold: cost is unknown


async def test_concurrency_is_bounded_by_the_semaphore():
    async def measure(ops: int) -> int:
        inflight = peak = polls = 0

        def post(_request):
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            return json_response(201, created())

        def operations(_request):
            nonlocal inflight, polls
            polls += 1
            if polls % 2:  # every run sees one pending poll, then its terminal one
                return op("op_pending.json")
            inflight -= 1
            return op("op_success.json")

        async def yield_loop(_: float) -> None:
            await asyncio.sleep(0)

        transport = RecordingTransport({POST: post, op_url(): operations})
        executor, _, _ = make(transport, ops=ops, sleep=yield_loop)
        await asyncio.gather(
            *(executor.run(IMAGE, ARGV, network=False, timeout_s=10) for _ in range(4))
        )
        return peak

    assert await measure(1) == 1
    assert await measure(4) > 1  # the probe can see concurrency, so the bound is real


# ---- metering -----------------------------------------------------------------------------


async def test_reported_cost_is_recorded_on_the_budget():
    bud = budget()
    executor, _, _ = make(RecordingTransport(routes("op_saved.json")))
    result = await executor.run(IMAGE, ARGV, network=False, timeout_s=10, save=True, budget=bud)
    reported = fixture_json("contree", "op_saved.json")["metadata"]["result"]["resources"]["cost"]
    assert result.cost_usd == reported > 0
    assert bud.usage_usd()["nebius"] == pytest.approx(reported)  # hold swapped for the actual


async def test_without_a_reported_cost_the_duration_times_the_rate_is_used():
    data = fixture_json("contree", "op_success.json")
    del data["metadata"]["result"]["resources"]["cost"]
    data["duration"] = 2.5
    transport = RecordingTransport(routes(extra={op_url(): json_response(200, data)}))
    bud = budget()
    executor, _, _ = make(transport)
    result = await executor.run(IMAGE, ARGV, network=False, timeout_s=10, budget=bud)
    assert result.cost_usd == pytest.approx(2.5 * RATE)
    assert bud.usage_usd()["nebius"] == pytest.approx(2.5 * RATE)


async def test_the_hold_is_ceil_timeout_times_the_rate():
    holds: list[float] = []
    bud = budget()
    real = bud.try_spend

    def spy(pool, estimate):
        holds.append(estimate)
        return real(pool, estimate)

    bud.try_spend = spy  # type: ignore[method-assign]
    executor, _, _ = make()
    await executor.run(IMAGE, ARGV, network=False, timeout_s=119.2, budget=bud)
    assert holds == [pytest.approx(120 * RATE)]


async def test_spend_denied_stops_the_run_before_any_request():
    bud = budget(nebius=0.0)  # nothing reserved
    executor, _, transport = make()
    with pytest.raises(ExecutorSpendDenied) as caught:
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10, budget=bud)
    assert caught.value.denied.pool == "nebius"
    assert caught.value.denied.reason == "scan_budget_reached"
    assert transport.requests == []


async def test_a_failure_after_the_operation_started_keeps_the_hold():
    transport = RecordingTransport(routes(extra={op_url(): httpx.Response(500)}))
    bud = budget()
    executor, _, _ = make(transport)
    with pytest.raises(ExecutorUnavailable):
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10, budget=bud)
    assert bud.usage_usd()["nebius"] == pytest.approx(10 * RATE)


async def test_a_failure_before_the_operation_started_costs_nothing():
    transport = RecordingTransport({POST: httpx.Response(401, json={})})
    bud = budget()
    executor, _, _ = make(transport)
    with pytest.raises(ExecutorUnavailable):
        await executor.run(IMAGE, ARGV, network=False, timeout_s=10, budget=bud)
    assert bud.usage_usd()["nebius"] == 0.0


# ---- prepare and the base image ------------------------------------------------------------


async def test_split_base_image_imports_the_version_tag_and_names_the_local_tag_by_digest():
    digest = "a" * 64
    assert split_base_image(
        f"ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:{digest}"
    ) == (
        "ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim",
        f"pir-base/sha256-{digest}",
    )
    for bad in ("ghcr.io/astral-sh/uv:python3.13", "x@sha256:abc", "", f"@sha256:{digest}"):
        with pytest.raises(ExecutorUnavailable):
            split_base_image(bad)


async def test_prepare_imports_the_version_tag_once_and_runs_prepare_cmd_with_files():
    executor, sdk, transport = make()
    files = {"/opt/pir/agent.pyz": b"zip", "/opt/pir/src.tar.gz": b"tar"}
    ref = await executor.prepare(files)
    settings = Settings(_env_file=None)
    assert len(sdk.oci_calls) == 1
    version_tag, digest = settings.sandbox_base_image.split("@sha256:")
    assert (
        sdk.oci_calls[0]["ref"]
        == version_tag
        == "ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim"
    )
    assert "@" not in sdk.oci_calls[0]["ref"]  # the importer 404s on any @sha256 reference
    assert sdk.oci_calls[0]["tag"] == f"pir-base/sha256-{digest}"
    assert len(sdk.run_calls) == 1
    call = sdk.run_calls[0]
    assert call["shell"] == PREPARE_CMD and call["disposable"] is False
    assert call["files"] == files
    assert ref.kind == "sandbox" and ref.ref == sdk.produced[0]
    assert transport.requests == []  # prepare never touches REST


async def test_prepare_is_cached_by_the_sha256_of_the_files_and_base_import_by_process():
    executor, sdk, _ = make()
    first = await executor.prepare({"/opt/pir/a": b"1"})
    again = await executor.prepare({"/opt/pir/a": b"1"})
    other = await executor.prepare({"/opt/pir/a": b"2"})
    assert first == again != other
    assert len(sdk.run_calls) == 2
    assert len(sdk.oci_calls) == 1
    assert files_key({"/opt/pir/a": b"1"}) != files_key({"/opt/pir/a": b"2"})
    assert files_key({"/opt/pir/a": b"1", "/opt/pir/b": b"2"}) == files_key(
        {"/opt/pir/b": b"2", "/opt/pir/a": b"1"}
    )


async def test_the_sdk_run_is_used_by_prepare_only_with_the_constant_command():
    executor, sdk, transport = make(RecordingTransport(routes("op_success.json")))
    image = await executor.prepare({"/opt/pir/agent.pyz": b"zip"})
    await executor.run(image, ARGV, network=False, timeout_s=10)
    await executor.run(image, ARGV, network=True, timeout_s=10)
    assert [c["shell"] for c in sdk.run_calls] == [PREPARE_CMD]
    assert len([r for r in transport.requests if r.method == "POST"]) == 2


async def test_concurrent_prepares_of_the_same_files_run_once():
    executor, sdk, _ = make()
    refs = await asyncio.gather(*(executor.prepare({"/opt/pir/a": b"1"}) for _ in range(3)))
    assert len(set(refs)) == 1 and len(sdk.run_calls) == 1


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"/etc/passwd": b"x"},
        {"/opt/pir/../etc/x": b"x"},
        {"opt/pir/a": b"x"},
        {"/opt/pir/a": "s"},
    ],
)
async def test_prepare_rejects_bad_files(files):
    executor, sdk, _ = make()
    with pytest.raises(ValueError):
        await executor.prepare(files)
    assert sdk.run_calls == [] and sdk.oci_calls == []


async def test_sdk_failures_become_unavailable_and_never_leak_the_credentials():
    sdk = FakeContree()
    sdk.fail_oci = RuntimeError(f"401 for token {API_KEY} project {PROJECT}")
    executor, _, _ = make(sdk=sdk)
    with pytest.raises(ExecutorUnavailable) as caught:
        await executor.prepare({"/opt/pir/a": b"1"})
    text = str(caught.value)
    assert "RuntimeError" in text and API_KEY not in text and PROJECT not in text


async def test_a_failing_prepare_command_is_unavailable():
    sdk = FakeContree()
    sdk.run_exit_code = 1
    executor, _, _ = make(sdk=sdk)
    with pytest.raises(ExecutorUnavailable, match="exit code 1"):
        await executor.prepare({"/opt/pir/a": b"1"})
    sdk = FakeContree()
    sdk.fail_run = RuntimeError("boom")
    executor, _, _ = make(sdk=sdk)
    with pytest.raises(ExecutorUnavailable, match="prepare failed"):
        await executor.prepare({"/opt/pir/a": b"1"})


async def test_missing_credentials_are_unavailable_without_a_client():
    executor = ContreeExecutor(Settings(_env_file=None, nebius_api_key=None))
    with pytest.raises(ExecutorUnavailable, match="NEBIUS_API_KEY"):
        await executor.prepare({"/opt/pir/a": b"1"})


# ---- discard ------------------------------------------------------------------------------


async def test_discard_untags_the_saved_image():
    image = ImageRef(kind="sandbox", ref="1cd3e740-bbc4-42d6-957d-4da1fc3d328f")
    delete = f"DELETE {BASE}/v1/images/{image.ref}/tag"
    transport = RecordingTransport({delete: httpx.Response(200, json={})})
    executor, _, _ = make(transport)
    await executor.discard(image)
    assert transport.urls == [delete]


@pytest.mark.parametrize(
    "outcome", [httpx.Response(500, text="no"), httpx.ConnectError("down"), RuntimeError("odd")]
)
async def test_discard_failures_are_logged_not_raised(outcome, caplog):
    image = ImageRef(kind="sandbox", ref="1cd3e740-bbc4-42d6-957d-4da1fc3d328f")
    delete = f"DELETE {BASE}/v1/images/{image.ref}/tag"
    executor, _, _ = make(RecordingTransport({delete: outcome}))
    with caplog.at_level(logging.WARNING, logger="pir.executor.contree"):
        await executor.discard(image)
    assert "untagging an image failed" in caplog.text


async def test_discard_ignores_other_kinds_and_bad_refs():
    executor, _, transport = make()
    await executor.discard(ImageRef(kind="fake", ref="/tmp/x"))
    await executor.discard(ImageRef(kind="sandbox", ref="../../etc"))
    assert transport.requests == []


def test_the_live_tests_use_contree_executor_directly_not_a_tag_workaround():  # ADR-0033
    from pathlib import Path

    live = Path(__file__).resolve().parents[1] / "live" / "test_live_sandbox.py"
    text = live.read_text(encoding="utf-8")
    assert "TagBaseExecutor" not in text and "ContreeExecutor(settings)" in text
    assert "test_base_image_fingerprint_matches" in text
    assert "test_images_oci_accepts_digest" not in text
