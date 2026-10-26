# Evaluation: PASS (LR precision 1.00, bar 0.80)

- Run date: 2026-10-26
- Model: nvidia/nemotron-3-super-120b-a12b
- Experiment: eval-20261026T095308Z-nvidia-nemotron-3-super-120b-a12b
- Scored run: yes
- Advisory recordings: replayed

| Metric | Value |
|---|---|
| N (cases) | 14 |
| LR precision | 1.00 |
| LR recall | 0.86 |
| Abstention rate | 0.29 |
| Correct abstentions | 3 |
| No-path precision | 1.00 |
| Dangerous misses | 0 |
| Errored cases | 0 |

Confusion matrix (rows: label, columns: predicted)

| label \ predicted | likely reachable | no reachable path detected | uncertain |
|---|---|---|---|
| likely reachable | 6 | 0 | 1 |
| no reachable path detected | 0 | 4 | 0 |
| uncertain | 0 | 0 | 3 |

| Case | Label | Predicted | Status | Correct |
|---|---|---|---|---|
| yaml-direct-call | likely reachable | likely reachable | scored | yes |
| yaml-import-as-alias | likely reachable | likely reachable | scored | yes |
| yaml-from-import-as-alias | likely reachable | likely reachable | scored | yes |
| yaml-call-chain-cli | likely reachable | likely reachable | scored | yes |
| yaml-imported-never-called | no reachable path detected | no reachable path detected | scored | yes |
| yaml-never-imported | no reachable path detected | no reachable path detected | scored | yes |
| yaml-dynamic-getattr | uncertain | uncertain | scored | yes |
| requests-session-verify-false | likely reachable | uncertain | scored | no |
| requests-extract-zipped-paths-unused | no reachable path detected | no reachable path detected | scored | yes |
| requests-netrc-no-function | uncertain | uncertain | scored | yes |
| wheel-no-function | uncertain | uncertain | scored | yes |
| co2tools-yaml-safeloader | no reachable path detected | no reachable path detected | scored | yes |
| pyrl-yaml-load-default-loader-14343 | likely reachable | likely reachable | scored | yes |
| pyrl-yaml-load-default-loader-1747 | likely reachable | likely reachable | scored | yes |

Limitations: 14 labelled cases from 11 repositories; 11 of them (79%) use local synthetic fixtures and 3 use pinned real repositories. Advisory data (OSV and Tavily answers) is recorded and replayed from files, not fetched live. Single model (nvidia/nemotron-3-super-120b-a12b), temperature 0, one run. With this few cases one case moves precision by several points.
LangSmith read-back: 14 of 14 case traces.
