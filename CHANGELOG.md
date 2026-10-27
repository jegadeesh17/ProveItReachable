# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Token Factory Sandbox execution environment with offline code analysis and non-root execution (`M1-TASK-08`).
- Nebius LLM client (Nemotron-3-Super-120B) with native tool calling and structured token metering (`M1-TASK-09`).
- AST analysis tool and tool-calling reasoning loop for reachability graph inspection (`M1-TASK-10`).
- Core scanning pipeline, dependency compilation, and LangSmith execution tracing (`M1-TASK-11`).
- Evaluation benchmark suite with 30 cases across 26 repositories and automated precision/recall scoring (`M1-TASK-12`, `M2-TASK-18`).
- HTTP API core with FastAPI, rate limiting, and security headers (`M2-TASK-01`).
- Hosted spend ledger on Hugging Face dataset with compare-and-swap concurrency checks (`M2-TASK-02`).
- Pre-recorded demo replays (`/?demo=<slug>`) serving real Sandbox proofs with zero runtime cost (`M2-TASK-03`, `M2-TASK-19`, `M2-TASK-20`).
- Markdown report export with sanitization and scheme verification (`M2-TASK-04`).
- Server-Sent Events (SSE) live progress feed (`M2-TASK-05`).
- Single-page web UI built with vanilla JS and strict design tokens (`M2-TASK-07`).
- Token Factory Sandbox call-chain execution proof harness with offline sdist building (`M2-TASK-09`, `M2-TASK-16`).
- Interactive call-chain proof controls and recorded proof badges in the UI (`M2-TASK-10`, `M2-TASK-20`).
- Fix-commit fallback in advisory research to extract vulnerable symbols from commit diffs with zero search spend (`M2-TASK-21`, ADR-0043).
- Deterministic finding remediation targets and upgrade guidance (`M2-TASK-22`, ADR-0044).
- Interactive native SVG call-path diagram with mobile responsiveness and text step fallback (`M2-TASK-23`, ADR-0044).
- Cryptographic proof-result authenticity nonce verification preventing repository-forged verdicts (`M2-TASK-24`, ADR-0044).
- Active judging-window budget profile with $0.75 reservation and daily scan pacing (`M2-TASK-25`, ADR-0044).

### Changed
- Verdict reachability gate updated with deterministic pairwise call edge continuity verification (`M2-TASK-17`, ADR-0042).
- Offline sdist dependency builds integrated into proof execution pipeline (`M2-TASK-16`, ADR-0041).
- Cloud Run deployment configuration and keepalive monitoring (`M2-TASK-08`, ADR-0040).

### Fixed
- Fixed call-path reachability evidence loophole by rejecting stitched or discontinuous AST paths (`M2-TASK-17`).
- Fixed proof runner result line authenticity by rejecting unauthenticated stdout outputs (`M2-TASK-24`).
- Fixed alert noise in demo repositories, reducing 66 scanner alerts down to 4 likely reachable findings (`scripts/noise_summary.py`).
