"""Research results (ARCHITECTURE section 6.4)."""

from pydantic import Field, model_validator

from pir.models import ReasonCode, Strict
from pir.models.report import NOT_IDENTIFIED_PREFIX, AdvisoryLink


class SearchResult(Strict):
    url: str
    title: str | None
    content: str = Field(max_length=2000)  # untrusted


class AdvisoryResearch(Strict):
    advisory_id: str
    identified: bool
    not_identified_reason: str | None
    from_cache: bool
    symbols: list[str]
    links: list[AdvisoryLink]
    evidence_urls: list[str]
    failure: ReasonCode | None

    @model_validator(mode="after")
    def _check(self) -> "AdvisoryResearch":
        if self.identified != bool(self.symbols):
            raise ValueError("symbols are non-empty iff identified")
        if not self.identified and not (self.not_identified_reason or "").startswith(
            NOT_IDENTIFIED_PREFIX
        ):
            raise ValueError(f"not_identified_reason must start with '{NOT_IDENTIFIED_PREFIX}'")
        if self.identified and self.not_identified_reason is not None:
            raise ValueError("not_identified_reason is set only when not identified")
        if not set(self.evidence_urls) <= {link.url for link in self.links}:
            raise ValueError("evidence_urls must be a subset of links")
        if self.failure not in (None, "tavily_failed", "nebius_failed"):
            raise ValueError("failure is tavily_failed or nebius_failed")
        return self
