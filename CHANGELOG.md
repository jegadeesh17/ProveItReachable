# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Token Factory Sandbox execution environment with offline code analysis and non-root execution.
- Nebius LLM client (Nemotron-3-Super-120B) with native tool calling and structured token metering.
- AST analysis tool and tool-calling reasoning loop for reachability graph inspection.
- Core scanning pipeline, dependency compilation, and LangSmith execution tracing.
- Evaluation benchmark suite with 30 cases across 26 repositories and automated precision/recall scoring.
- HTTP API core with FastAPI, rate limiting, and security headers.
- Hosted spend ledger on Hugging Face dataset with compare-and-swap concurrency checks.
- Pre-recorded demo replays (`/?demo=<slug>`) serving real Sandbox proofs with zero runtime cost.
- Markdown report export with sanitization and scheme verification.
- Server-Sent Events (SSE) live progress feed.
- Single-page web UI built with vanilla JS and strict design tokens.
- Token Factory Sandbox call-chain execution proof harness with offline sdist building.
- Interactive call-chain proof controls and recorded proof badges in the UI.
- Fix-commit fallback in advisory research to extract vulnerable symbols from commit diffs with zero search spend (ADR-0043).
- Deterministic finding remediation targets and upgrade guidance (ADR-0044).
- Interactive native SVG call-path diagram with mobile responsiveness and text step fallback (ADR-0044).
- Cryptographic proof-result authenticity nonce verification preventing repository-forged verdicts (ADR-0044).
- Active judging-window budget profile with $0.75 reservation and daily scan pacing (ADR-0044).

### Changed
- Verdict reachability gate updated with deterministic pairwise call edge continuity verification (ADR-0042).
- Offline sdist dependency builds integrated into proof execution pipeline (ADR-0041).
- Cloud Run deployment configuration and keepalive monitoring (ADR-0040).

### Fixed
- Fixed call-path reachability evidence loophole by rejecting stitched or discontinuous AST paths.
- Fixed proof runner result line authenticity by rejecting unauthenticated stdout outputs.
- Fixed alert noise in demo repositories, reducing 66 scanner alerts down to 4 likely reachable findings (`scripts/noise_summary.py`).

