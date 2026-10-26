"""Default-argument equivalences: the reviewed table and its one matcher (ADR-0037, AC-80)."""

import pytest

from pir.models.index import TargetSite
from pir.reasoning.equivalences import (
    EQUIVALENCES,
    implied_symbols,
    normalize_package,
    parse_version,
)

LOAD_IMPLIES = frozenset({"yaml.full_load", "yaml.FullLoader"})
LOAD_ALL_IMPLIES = frozenset({"yaml.full_load_all", "yaml.FullLoader"})


def site(target="yaml.load", args=1, keywords=(), kind="call"):
    return TargetSite(
        file="main.py",
        line=3,
        col=0,
        caller="main:<module>",
        kind=kind,
        snippet="yaml.load(f)",
        target=target,
        args=args,
        keywords=None if keywords is None else list(keywords),
    )


# --- positives ---


@pytest.mark.parametrize("version", ["5.1", "5.3", "5.3.1", "5.4.1", "5.4.1.1", "v5.3", "5.3rc1"])
def test_yaml_load_without_loader_implies_full_load_in_range(version):  # AC-80
    assert implied_symbols(site(), "PyYAML", version) == LOAD_IMPLIES


def test_yaml_load_all_without_loader_implies_full_load_all():  # AC-80
    assert implied_symbols(site("yaml.load_all"), "PyYAML", "5.3") == LOAD_ALL_IMPLIES


def test_no_arguments_at_all_still_implies():  # AC-80
    assert implied_symbols(site(args=0), "pyyaml", "5.3") == LOAD_IMPLIES


@pytest.mark.parametrize("name", ["PyYAML", "pyyaml", "PYYAML", "  pyyaml "])
def test_package_name_is_normalised(name):  # AC-80
    assert implied_symbols(site(), name, "5.3") == LOAD_IMPLIES


def test_other_keywords_do_not_stop_the_match():  # AC-80
    assert implied_symbols(site(keywords=["foo"]), "PyYAML", "5.3") == LOAD_IMPLIES


# --- negatives: precision first ---


def test_a_loader_keyword_never_matches():  # AC-80
    assert implied_symbols(site(keywords=["Loader"]), "PyYAML", "5.3") == frozenset()


def test_a_positional_loader_never_matches():  # AC-80
    assert implied_symbols(site(args=2), "PyYAML", "5.3") == frozenset()
    assert implied_symbols(site(args=3), "PyYAML", "5.3") == frozenset()


def test_star_args_never_matches():  # AC-80
    assert implied_symbols(site(args=None), "PyYAML", "5.3") == frozenset()


def test_star_star_kwargs_never_matches():  # AC-80
    assert implied_symbols(site(keywords=None), "PyYAML", "5.3") == frozenset()


def test_a_reference_never_matches():  # AC-80
    ref = site(args=None, keywords=None, kind="reference")
    assert implied_symbols(ref, "PyYAML", "5.3") == frozenset()


@pytest.mark.parametrize("version", ["6.0", "6.0.1", "6", "7.0", "5.0", "5.0.9", "4.2"])
def test_versions_outside_the_range_imply_nothing(version):  # AC-80
    assert implied_symbols(site(), "PyYAML", version) == frozenset()


@pytest.mark.parametrize(
    "version",
    [None, "", "  ", "latest", "unknown", "x5.3", "5..3", "5.3.", ".5", "5.3 1", "5.3.1.", "5.3!"],
)
def test_an_unparsable_version_implies_nothing(version):  # AC-80
    assert implied_symbols(site(), "PyYAML", version) == frozenset()


def test_no_package_applies_no_equivalence():  # AC-80
    assert implied_symbols(site(), None, "5.3") == frozenset()


@pytest.mark.parametrize("package", ["requests", "", "pyyaml-extras", "yaml"])
def test_another_package_implies_nothing(package):  # AC-80
    assert implied_symbols(site(), package, "5.3") == frozenset()


@pytest.mark.parametrize("target", ["yaml.safe_load", "yaml.full_load", "yaml.load_x", "yaml"])
def test_another_call_implies_nothing(target):  # AC-80
    assert implied_symbols(site(target), "PyYAML", "5.3") == frozenset()


# --- helpers and table ---


def test_parse_version_pads_and_rejects():
    assert parse_version("5.3") == (5, 3, 0)
    assert parse_version("5.3.1") == (5, 3, 1)
    assert parse_version("5.3rc1") == (5, 3, 0)
    assert parse_version("garbage") is None
    assert parse_version(None) is None


def test_normalize_package():
    assert normalize_package("Py_YAML.x") == "py-yaml-x"


def test_every_row_is_pyyaml_with_a_non_empty_range_and_implications():
    assert len(EQUIVALENCES) == 2
    for row in EQUIVALENCES:
        assert row.package == normalize_package(row.package)
        assert row.min_version < row.max_version
        assert row.implies
