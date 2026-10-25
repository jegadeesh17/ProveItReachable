"""Live OSV check: query one known-vulnerable pin and parse the advisory details.

Runs only with PIR_LIVE_TESTS=1 (slow + live). Uses a temporary DATA_DIR so the cache is clean.
"""

import pytest

from pir.config import Settings
from pir.models.report import Advisory, AdvisoryLink, Dependency
from pir.osv import OsvClient

pytestmark = [pytest.mark.slow, pytest.mark.live]

# PyYAML 5.3.1 is affected by CVE-2020-14343 (GHSA-8q59-q68h-6hv4 / PYSEC-2021-142).
_PIN = Dependency(
    name="pyyaml",
    source="uv.lock",
    groups=["main"],
    version="5.3.1",
    resolved=True,
    unresolved_reason=None,
    direct=True,
    osv_status="checked",
    osv_error=None,
)


async def test_live_osv_known_vulnerable_pin_parses_details(tmp_path):
    client = OsvClient(Settings(_env_file=None, data_dir=str(tmp_path)))
    result = await client.query([_PIN])
    ids = result["pyyaml==5.3.1"]
    assert isinstance(ids, list) and ids, f"expected advisories for pyyaml 5.3.1, got {ids!r}"

    advisory = await client.get_advisory(ids[0])
    assert isinstance(advisory, Advisory)
    assert advisory.id == ids[0]
    assert advisory.summary or advisory.aliases
    links = client.links(ids[0])
    assert links and all(isinstance(link, AdvisoryLink) for link in links)
    assert links[0].url == f"https://osv.dev/vulnerability/{ids[0]}"
    assert (tmp_path / "cache" / "osv" / f"{ids[0]}.json").exists()
