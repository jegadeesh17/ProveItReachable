"""Load `eval/cases.toml` into `EvalRepo` and `EvalCase` models (ARCHITECTURE section 15)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from pir.models.eval import EvalCase, EvalRepo
from pir.osv import is_valid_osv_id

LOCAL_PREFIX = "local:"


class CasesError(ValueError):
    """`cases.toml` is missing, malformed or inconsistent. The message names the problem."""


@dataclass(frozen=True)
class EvalSuite:
    repos: list[EvalRepo]
    cases: list[EvalCase]
    root: Path  # the project root that `local:` sources are relative to

    def repo(self, repo_id: str) -> EvalRepo:
        for repo in self.repos:
            if repo.id == repo_id:
                return repo
        raise KeyError(repo_id)

    def local_path(self, repo: EvalRepo) -> Path:
        """The fixture directory of a `local:` repo."""
        if not repo.source.startswith(LOCAL_PREFIX):
            raise ValueError(f"repo {repo.id} is not a local fixture")
        return self.root / repo.source[len(LOCAL_PREFIX) :]


def _tables(data: dict, name: str) -> list[dict]:
    value = data.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise CasesError(f"[[{name}]] must be an array of tables")
    return value


def load_cases(path: str | Path, root: str | Path | None = None) -> EvalSuite:
    """Parse and cross-check the cases file.

    `root` defaults to the parent of the file's directory (`eval/cases.toml` -> project root).
    Raises CasesError for a missing file, bad TOML, an invalid table, a duplicate id, a case that
    names an undefined repo, an OSV ID that is not valid, or a missing local fixture directory.
    """
    cases_path = Path(path)
    try:
        data = tomllib.loads(cases_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CasesError(f"cannot read {cases_path}: {type(exc).__name__}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CasesError(f"{cases_path} is not valid TOML: {exc}") from exc
    project_root = Path(root) if root is not None else cases_path.resolve().parent.parent
    repos: list[EvalRepo] = []
    cases: list[EvalCase] = []
    try:
        repos = [EvalRepo.model_validate(item) for item in _tables(data, "repo")]
        cases = [EvalCase.model_validate(item) for item in _tables(data, "case")]
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise CasesError(f"invalid table in {cases_path.name}: {where}: {first['msg']}") from exc
    for kind, ids in (("repo", [r.id for r in repos]), ("case", [c.id for c in cases])):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise CasesError(f"duplicate {kind} id: {', '.join(duplicates)}")
    repo_ids = {r.id for r in repos}
    for case in cases:
        if case.repo not in repo_ids:
            raise CasesError(f"case {case.id} names an undefined repo: {case.repo}")
        if not is_valid_osv_id(case.advisory):
            raise CasesError(f"case {case.id} has an invalid OSV id: {case.advisory!r}")
    suite = EvalSuite(repos=repos, cases=cases, root=project_root)
    for repo in repos:
        if repo.source.startswith(LOCAL_PREFIX) and not suite.local_path(repo).is_dir():
            raise CasesError(f"repo {repo.id}: fixture directory {repo.source} does not exist")
    return suite
