# Evaluation: PASS (LR precision 0.91, bar 0.80)

- Run date: 2026-10-26
- Model: nvidia/nemotron-3-super-120b-a12b
- Experiment: eval-20261026T145343Z-nvidia-nemotron-3-super-120b-a12b
- Scored run: yes
- Advisory recordings: re-recorded live

| Metric | Value |
|---|---|
| N (cases) | 30 |
| LR precision | 0.91 |
| LR recall | 0.67 |
| Abstention rate | 0.30 |
| Correct abstentions | 3 |
| No-path precision | 0.60 |
| Dangerous misses | 1 |
| Errored cases | 4 |

Confusion matrix (rows: label, columns: predicted)

| label \ predicted | likely reachable | no reachable path detected | uncertain |
|---|---|---|---|
| likely reachable | 10 | 1 | 4 |
| no reachable path detected | 1 | 6 | 2 |
| uncertain | 0 | 3 | 3 |

| Case | Label | Predicted | Status | Correct |
|---|---|---|---|---|
| yaml-direct-call | likely reachable | likely reachable | scored | yes |
| yaml-import-as-alias | likely reachable | likely reachable | scored | yes |
| yaml-from-import-as-alias | likely reachable | likely reachable | scored | yes |
| yaml-call-chain-cli | likely reachable | likely reachable | scored | yes |
| yaml-imported-never-called | no reachable path detected | no reachable path detected | scored | yes |
| yaml-never-imported | no reachable path detected | no reachable path detected | scored | yes |
| yaml-dynamic-getattr | uncertain | no reachable path detected | scored | no |
| requests-session-verify-false | likely reachable | uncertain | scored | no |
| requests-extract-zipped-paths-unused | no reachable path detected | no reachable path detected | scored | yes |
| requests-netrc-no-function | uncertain | uncertain | scored | yes |
| wheel-no-function | uncertain | uncertain | scored | yes |
| co2tools-yaml-safeloader | no reachable path detected | no reachable path detected | scored | yes |
| pyrl-yaml-load-default-loader-14343 | likely reachable | likely reachable | scored | yes |
| pyrl-yaml-load-default-loader-1747 | likely reachable | likely reachable | scored | yes |
| yaml-multi-hop | likely reachable | likely reachable | scored | yes |
| yaml-class-method | likely reachable | likely reachable | scored | yes |
| yaml-decorator-wrapped | likely reachable | likely reachable | scored | yes |
| yaml-init-reexport | likely reachable | uncertain | scored | no |
| yaml-conditional-import | likely reachable | likely reachable | scored | yes |
| jinja2-env-from-string | likely reachable | uncertain | missing | no |
| urllib3-pool-request | likely reachable | uncertain | scored | no |
| yaml-test-only | no reachable path detected | no reachable path detected | scored | yes |
| yaml-string-mention-only | no reachable path detected | no reachable path detected | scored | yes |
| yaml-dead-code-if-false | no reachable path detected | likely reachable | scored | no |
| jinja2-escape-only | no reachable path detected | uncertain | missing | no |
| urllib3-unused-internals | no reachable path detected | uncertain | missing | no |
| yaml-importlib-dynamic | uncertain | no reachable path detected | scored | no |
| jinja2-custom-loader-dynamic | uncertain | uncertain | missing | yes |
| planesweeppose-yaml-load | likely reachable | no reachable path detected | scored | no |
| planesweeppose-torch-unpack | uncertain | no reachable path detected | scored | no |

Errored or unmatched cases (scored as uncertain):

- jinja2-env-from-string: no finding for jinja2 / GHSA-g3rq-g295-48cu in the report
- jinja2-escape-only: no finding for jinja2 / GHSA-g3rq-g295-48cu in the report
- urllib3-unused-internals: no finding for urllib3 / GHSA-q2x7-8rv6-6q7h in the report
- jinja2-custom-loader-dynamic: no finding for jinja2 / GHSA-g3rq-g295-48cu in the report

Limitations: 30 labelled cases from 26 repositories; 25 of them (83%) use local synthetic fixtures and 5 use pinned real repositories. Advisory data (OSV and Tavily answers) is recorded and replayed from files, not fetched live. Single model (nvidia/nemotron-3-super-120b-a12b), temperature 0, one run. With this few cases one case moves precision by several points.
LangSmith read-back: 30 of 30 case traces.
