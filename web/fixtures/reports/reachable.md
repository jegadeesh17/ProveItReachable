# ProveItReachable report: placeholder-demo/flask-notes

- Repository: https://github.com/placeholder-demo/flask-notes
- Commit: a584296c4268f7536fae191dc01381a0e249dcab
- Outcome: findings
- Generated: 2026-10-12T09:14:52Z by ProveItReachable 0.1.0-prototype (model nvidia/nemotron-3-super-120b-a12b)
- Dependencies: 12 total, 12 checked, 0 unresolved, 0 not checked

> Prototype fixture: the repository, versions and findings are placeholders, not measurements.

## Summary

- likely reachable: 2
- uncertain: 2
- no reachable path detected: 2

## Findings

### 1. likely reachable: pyyaml 5.3.1 (GHSA-8q59-q68h-6hv4)

**Evidence**

- Vulnerable function: `yaml.load`
- Call path:
  1. `app/main.py:40` app.main:cli (entry)
  2. `app/main.py:44` app.config:read_config (call)
  3. `app/config.py:12` yaml.load (vulnerable call)
- Source locations:
  - `app/config.py:3` import: `import yaml as y`
  - `app/config.py:12` call: `cfg = y.load(fh)`
- Advisory links:
  - https://osv.dev/vulnerability/GHSA-8q59-q68h-6hv4 (osv, OSV record GHSA-8q59-q68h-6hv4)
  - https://github.com/advisories/GHSA-8q59-q68h-6hv4 (osv, GitHub Advisory Database: GHSA-8q59-q68h-6hv4)
  - https://nvd.nist.gov/vuln/detail/CVE-2020-14343 (tavily, NVD: CVE-2020-14343)

**Confidence:** high

**Limitations**

- Static analysis of 14 of 15 Python files (1 skipped: syntax error). Dynamic imports, getattr and reflection were not examined.
- The path was found statically and was not run.

**Explanation**

The command-line entry point cli calls read_config, which passes an open file to yaml.load without naming a safe loader. The call goes through the alias y, so a plain text search for yaml.load would have missed it. The file path is read from a command-line option at app/main.py:42, so a crafted YAML file given to the tool reaches the vulnerable loader.

### 2. likely reachable: jinja2 2.11.2 (GHSA-g3rq-g295-4j3m)

**Evidence**

- Vulnerable function: `jinja2.utils.urlize`
- Call path:
  1. `app/web.py:52` app.web:show_note (entry)
  2. `app/web.py:56` app.render:render_note (heuristic call)
  3. `app/render.py:33` jinja2.utils.urlize (vulnerable call)
- Source locations:
  - `app/render.py:4` import: `from jinja2.utils import urlize`
  - `app/render.py:33` call: `body_html = '<p class="note">' + urlize(note.body) + '</p>'`
- Advisory links:
  - https://osv.dev/vulnerability/GHSA-g3rq-g295-4j3m (osv, OSV record GHSA-g3rq-g295-4j3m)
  - https://nvd.nist.gov/vuln/detail/CVE-2020-28493 (tavily, NVD: CVE-2020-28493)

**Confidence:** medium

**Limitations**

- Static analysis of 14 of 15 Python files (1 skipped: syntax error). Dynamic imports, getattr and reflection were not examined.
- The path was found statically and was not run.
- One edge in the call path is heuristic (matched by name).

**Explanation**

The route show_note renders each note through render_note, which calls urlize on the note body. The note body is user-written text, so a crafted note can feed the slow pattern. The step from show_note to render_note was matched by method name, not by type, so it is marked heuristic and confidence stops at medium.

### 3. uncertain: flask 1.1.2 (GHSA-m2qf-hxjv-5gpq)

**Evidence**

- Vulnerable function: not identified (vulnerable function not identified: the advisory describes a caching condition and names no function or API.)
- Source locations:
  - `app/__init__.py:2` import: `from flask import Flask`
  - `app/__init__.py:9` call: `app = Flask(__name__)`
- Advisory links:
  - https://osv.dev/vulnerability/GHSA-m2qf-hxjv-5gpq (osv, OSV record GHSA-m2qf-hxjv-5gpq)

**Confidence:** low

**Reason:** vulnerable function not identified: the advisory names no function or API to search for. (vulnerable_function_not_identified)

**Limitations**

- No vulnerable function was identified, so no call-path analysis was run for this advisory.

**Explanation**

The advisory describes a condition that depends on a caching proxy in front of the app and on how sessions are used, not on a function the code calls. Flask is imported and the app object is created, but without a named function there is no call to trace. This needs a human reading of the deployment.

### 4. uncertain: urllib3 1.26.4 (GHSA-q2q7-5pp4-w6pg)

**Evidence**

- Vulnerable function: `urllib3.util.url.parse_url`
- Source locations: none found in repository code
- Advisory links:
  - https://osv.dev/vulnerability/GHSA-q2q7-5pp4-w6pg (osv, OSV record GHSA-q2q7-5pp4-w6pg)
  - https://nvd.nist.gov/vuln/detail/CVE-2021-33503 (tavily, NVD: CVE-2021-33503)

**Confidence:** low

**Reason:** The model answered likely reachable, but its call path failed the check: app/fetch.py:19 did not appear in any analysis tool result. (unsupported_reachability_claim)

**Limitations**

- No import or call site of urllib3 was found in repository code; it is used only through requests.
- Static analysis of 14 of 15 Python files (1 skipped: syntax error). Dynamic imports, getattr and reflection were not examined.

**Explanation**

The model first answered likely reachable through the requests library, but the call path it gave included app/fetch.py:19, which did not appear in any analysis tool result. The claim was not grounded, so the verdict was lowered to uncertain. Repository code never imports urllib3 directly.

### 5. no reachable path detected: requests 2.25.0 (GHSA-j8r2-6x86-q33q)

**Evidence**

- Vulnerable function: `requests.sessions.SessionRedirectMixin.rebuild_proxies`
- Source locations:
  - `app/fetch.py:4` import: `import requests`
  - `app/fetch.py:17` call: `resp = requests.get(url, timeout=5)`
- Advisory links:
  - https://osv.dev/vulnerability/GHSA-j8r2-6x86-q33q (osv, OSV record GHSA-j8r2-6x86-q33q)
  - https://nvd.nist.gov/vuln/detail/CVE-2023-32681 (tavily, NVD: CVE-2023-32681)

**Confidence:** medium

**Limitations**

- Scope: 14 of 15 Python files parsed (1 skipped: syntax error); examined imports, calls and references of requests in repository code. Not examined: dynamic imports, getattr or reflection, code inside other dependencies, C extensions, runtime configuration.

**Explanation**

Repository code calls requests.get once. The vulnerable function, rebuild_proxies, is internal to requests and no repository code names it. Whether the vulnerable branch runs depends on proxy settings and redirects at run time, which static analysis cannot see. Within that limit, no reachable path was detected.

### 6. no reachable path detected: cryptography 3.3.1 (GHSA-rhm9-p9w5-fwm7)

**Evidence**

- Vulnerable function: `cryptography.fernet.Fernet.encrypt`
- Source locations: none found in repository code
- Advisory links:
  - https://osv.dev/vulnerability/GHSA-rhm9-p9w5-fwm7 (osv, OSV record GHSA-rhm9-p9w5-fwm7)
  - https://nvd.nist.gov/vuln/detail/CVE-2020-36242 (tavily, NVD: CVE-2020-36242)

**Confidence:** low

**Limitations**

- No import or call site found in repository code.
- Scope: 14 of 15 Python files parsed (1 skipped: syntax error); examined imports, calls and references of cryptography in repository code. Not examined: dynamic imports, getattr or reflection, code inside other dependencies (this is a transitive dependency), C extensions, runtime configuration.

**Explanation**

No repository file imports cryptography or Fernet. The package is present only as a dependency of another package, so confidence is capped at low.

## Method and limitations

- Resolved from uv.lock; 12 packages pinned.
- Prototype fixture: the repository, versions, locations and findings are placeholders, not measurements. Advisory identifiers are real; the code evidence is invented for the demo.
- Only public Python repositories are examined. Code is read and indexed, never run.
- Advisories come from OSV for the exact resolved versions; a vulnerability OSV does not list cannot be found.
- Reachability is judged by static analysis of the repository’s own code. Dynamic imports, reflection and runtime configuration are not examined.
