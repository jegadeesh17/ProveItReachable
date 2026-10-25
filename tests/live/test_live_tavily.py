"""Live Tavily check (ARCHITECTURE 7.4): one basic search. Run with PIR_LIVE_TESTS=1 (slow + live).

Costs one Tavily credit. The key comes from Settings (TAVILY_API_KEY).
"""

import pytest

from pir.config import Settings
from pir.models.research import SearchResult
from pir.research import TavilySearchClient

pytestmark = [pytest.mark.slow, pytest.mark.live]


async def test_live_tavily_one_basic_search():
    client = TavilySearchClient(Settings())
    results = await client.search(
        "pyyaml GHSA-8q59-q68h-6hv4 CVE-2020-14343 vulnerable function",
        max_results=3,
        depth="basic",
        timeout_s=30,
    )
    assert results, "Tavily returned no results"
    assert len(results) <= 3
    for result in results:
        assert isinstance(result, SearchResult)
        assert result.url.startswith(("http://", "https://"))
        assert len(result.content) <= 2000
