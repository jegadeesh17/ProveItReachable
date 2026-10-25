# ProveItReachable report: placeholder-demo/legacy-reqs

- Repository: <https://github.com/placeholder-demo/legacy-reqs>
- Commit: `141c764abc7e94d70c2810dfe1600a2ebf14ad0f`
- Outcome: incomplete
- Generated: 2026-10-12T09:14:18Z by ProveItReachable 0.1.0-prototype (model nvidia/nemotron-3-super-120b-a12b)
- Dependencies: 7 total, 5 checked, 2 unresolved, 0 not checked

## Summary

- likely reachable: 0
- uncertain: 0
- no reachable path detected: 0

Not a clean result: some dependencies were not examined (see below).

## Dependencies not examined

- internal-metrics: unresolved. Declared as a git dependency over SSH \(git+ssh:&#47;/\); an exact version cannot be pinned without access to that repository.
- legacy-orm: unresolved. No distribution matches the constraint &gt;=2.0,&lt;2.1 for Python 3.12.

## Method and limitations

- Versions were compiled with uv pip compile, not locked.
- Prototype fixture: the repository, versions, locations and findings are placeholders, not measurements. Advisory identifiers are real; the code evidence is invented for the demo.
- Only public Python repositories are examined. Code is read and indexed, never run.
- Advisories come from OSV for the exact resolved versions; a vulnerability OSV does not list cannot be found.
- Reachability is judged by static analysis of the repository’s own code. Dynamic imports, reflection and runtime configuration are not examined.
