"""Default-argument equivalences (ARCHITECTURE section 9.1, ADR-0037).

A call that omits an argument can mean the same as a call of another function. Each row of the
reviewed table below says when, for one library version range. `implied_symbols` is the one
matcher used by the ast tool and the verdict gate alike, so a path the tool returns is a path the
gate accepts. It is pure and precision-first: anything it cannot be sure of implies nothing.

A row is added only with a test and a source link for the library's default.
"""

import re
from dataclasses import dataclass

from pir.models.index import TargetSite

_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)*)(?P<rest>.*)", re.DOTALL)
_PAD = 3  # compare release tuples at this width so (6,) and (6, 0) are equal


@dataclass(frozen=True)
class Equivalence:
    package: str  # normalised distribution name
    min_version: tuple[int, ...]  # inclusive
    max_version: tuple[int, ...]  # exclusive
    call: str  # the qualified name repository code calls
    keyword: str  # the parameter that, once passed, makes the call explicit
    positional: int  # the call passes it positionally when it has at least this many arguments
    implies: frozenset[str]


# PyYAML 5.1 added FullLoader and made it the default of load() and load_all() when no Loader is
# given (with a warning); 6.0 made the Loader argument required.
# https://github.com/yaml/pyyaml/blob/5.3/lib/yaml/__init__.py (def load(stream, Loader=None))
EQUIVALENCES: tuple[Equivalence, ...] = (
    Equivalence(
        package="pyyaml",
        min_version=(5, 1),
        max_version=(6, 0),
        call="yaml.load",
        keyword="Loader",
        positional=2,
        implies=frozenset({"yaml.full_load", "yaml.FullLoader"}),
    ),
    Equivalence(
        package="pyyaml",
        min_version=(5, 1),
        max_version=(6, 0),
        call="yaml.load_all",
        keyword="Loader",
        positional=2,
        implies=frozenset({"yaml.full_load_all", "yaml.FullLoader"}),
    ),
)


def normalize_package(name: str) -> str:
    """PEP 503 normalisation: lower case, runs of `-`, `_` and `.` become one `-`."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def parse_version(version: str | None) -> tuple[int, ...] | None:
    """The leading numeric release segments, padded; None when the text does not start like one.

    `5.3.1` and `5.3rc1` parse; `` and `latest` and `5..3` do not.
    """
    if not isinstance(version, str):
        return None
    match = _VERSION_RE.fullmatch(version.strip())
    if match is None:
        return None
    rest = match.group("rest")
    if rest and not (rest[0].isalpha() or rest[0] in "+-_"):
        return None
    parts = tuple(int(p) for p in match.group(1).split("."))
    return parts + (0,) * max(0, _PAD - len(parts))


def _pad(version: tuple[int, ...]) -> tuple[int, ...]:
    return version + (0,) * max(0, _PAD - len(version))


def implied_symbols(site: TargetSite, package: str | None, version: str | None) -> frozenset[str]:
    """Symbols the site stands for through a default argument, for the pinned package and version.

    Empty unless the site is a call with known argument facts (no `*args`, no `**kwargs`), that
    passes neither the keyword nor enough positional arguments, on a version inside the row's range.
    """
    if package is None or site.kind != "call" or site.args is None or site.keywords is None:
        return frozenset()
    parsed = parse_version(version)
    if parsed is None:
        return frozenset()
    normalized = normalize_package(package)
    implied: set[str] = set()
    for row in EQUIVALENCES:
        if (
            row.package == normalized
            and row.call == site.target
            and site.args < row.positional
            and row.keyword not in site.keywords
            and _pad(row.min_version) <= parsed < _pad(row.max_version)
        ):
            implied |= row.implies
    return frozenset(implied)
