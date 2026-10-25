# ProveItReachable report: placeholder-demo/data-pipeline

- Repository: <https://github.com/placeholder-demo/data-pipeline>
- Commit: `15a92e0c32ce027e77af0bcb12f1bc0483b6f849`
- Outcome: findings
- Generated: 2026-10-12T09:17:05Z by ProveItReachable 0.1.0-prototype (model nvidia/nemotron-3-super-120b-a12b)
- Dependencies: 9 total, 8 checked, 0 unresolved, 1 not checked

## Summary

- likely reachable: 0
- uncertain: 5
- no reachable path detected: 0

## Findings

### 1. uncertain: pyyaml 5.3.1 (GHSA-8q59-q68h-6hv4)

**Evidence**

- Vulnerable function: `yaml.load`
- Source locations: none found in repository code
- Advisory links:
  - <https://osv.dev/vulnerability/GHSA-8q59-q68h-6hv4> (osv, OSV record GHSA-8q59-q68h-6hv4)
  - <https://nvd.nist.gov/vuln/detail/CVE-2020-14343> (tavily, NVD: CVE-2020-14343)

**Confidence:** low

**Reason:** sandbox timeout: the code analysis exceeded its 120 s limit, so no call sites were found. (sandbox\_timeout)

**Limitations**

- No code analysis ran for this finding, so there is no evidence either way.

**Explanation**

Not assessed: the sandbox analysis timed out, so no call sites of yaml.load were found and the reasoning step did not run for this finding.

### 2. uncertain: urllib3 1.26.4 (GHSA-q2q7-5pp4-w6pg)

**Evidence**

- Vulnerable function: not identified (vulnerable function not identified: Nebius failed during advisory research.)
- Source locations: none found in repository code
- Advisory links:
  - <https://osv.dev/vulnerability/GHSA-q2q7-5pp4-w6pg> (osv, OSV record GHSA-q2q7-5pp4-w6pg)

**Confidence:** low

**Reason:** Nebius request timed out after 1 retry \(60 s\) during advisory research; no vulnerable function was determined. (nebius\_failed)

**Limitations**

- No code analysis ran for this finding, so there is no evidence either way.

**Explanation**

Not assessed: the model request timed out during advisory research, so the vulnerable function was not determined.

### 3. uncertain: jinja2 2.11.2 (GHSA-g3rq-g295-4j3m)

**Evidence**

- Vulnerable function: not identified (vulnerable function not identified: Tavily research failed.)
- Source locations: none found in repository code
- Advisory links:
  - <https://osv.dev/vulnerability/GHSA-g3rq-g295-4j3m> (osv, OSV record GHSA-g3rq-g295-4j3m)

**Confidence:** low

**Reason:** Tavily request timed out after 1 retry \(20 s\); advisory sources and the vulnerable function could not be researched. (tavily\_failed)

**Limitations**

- No code analysis ran for this finding, so there is no evidence either way.
- Only the OSV record could be linked; research sources are missing.

**Explanation**

Not assessed: web research for this advisory failed, so only the OSV record is available as a source.

### 4. uncertain: requests 2.25.0 (GHSA-j8r2-6x86-q33q)

**Evidence**

- Vulnerable function: `requests.sessions.SessionRedirectMixin.rebuild_proxies`
- Source locations: none found in repository code
- Advisory links:
  - <https://osv.dev/vulnerability/GHSA-j8r2-6x86-q33q> (osv, OSV record GHSA-j8r2-6x86-q33q)
  - <https://nvd.nist.gov/vuln/detail/CVE-2023-32681> (tavily, NVD: CVE-2023-32681)

**Confidence:** low

**Reason:** sandbox timeout: the code analysis exceeded its 120 s limit, so no call sites were found. (sandbox\_timeout)

**Limitations**

- No code analysis ran for this finding, so there is no evidence either way.

**Explanation**

Not assessed: the sandbox analysis timed out, so no imports or call sites of requests were indexed.

### 5. uncertain: pillow 8.2.0 (not checked)

**Evidence**

- No advisory was retrieved for this package, so there is no evidence to show.

**Confidence:** low

**Reason:** OSV did not answer after 2 retries \(15 s\); this package was not checked and must not be read as clean. (osv\_failed)

**Limitations**

- OSV returned no answer for pillow 8.2.0; its advisories, if any, are unknown.

**Explanation**

This package was not checked against OSV, so it cannot be reported as clean.

## Dependencies not examined

- pillow 8.2.0: not checked. OSV did not answer after 2 retries \(15 s\).

## Method and limitations

- Resolved from poetry.lock; 9 packages pinned.
- Prototype fixture: the repository, versions, locations and findings are placeholders, not measurements. Advisory identifiers are real; the code evidence is invented for the demo.
- Only public Python repositories are examined. Code is read and indexed, never run.
- Advisories come from OSV for the exact resolved versions; a vulnerability OSV does not list cannot be found.
- Reachability is judged by static analysis of the repository’s own code. Dynamic imports, reflection and runtime configuration are not examined.
