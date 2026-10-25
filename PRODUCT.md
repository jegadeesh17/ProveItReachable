# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Decided in `docs/ARCHITECTURE.md` (ADR-0012): plain HTML, CSS and JavaScript modules served by the FastAPI app. No framework, no build step. A `?api=mock` adapter with named scenarios drives the UI without the backend. Hosted on a free Hugging Face Docker Space.

## Users

- **Primary:** a Python developer or open-source maintainer whose dependency scanner reports dozens of known-vulnerable packages. They want to know which alerts actually matter: does their code reach the vulnerable function?
- **Evaluating audience:** hackathon judges (Nebius x NVIDIA Global AI Hackathon, Coding & Agentic track). They arrive cold, often on a laptop, and must understand the value in under 3 minutes, usually through a curated demo rather than a live scan.

## Product Purpose

ProveItReachable gives evidence-backed reachability triage for public Python GitHub repos. It resolves exact dependency versions, finds known advisories (OSV), researches the specific vulnerable function (Tavily), indexes the repo's code with deterministic static analysis, and has Nemotron reason over that evidence through tool calls. Each finding gets one of three verdicts: `likely reachable`, `no reachable path detected` or `uncertain`. Success means a developer can safely ignore most alerts and act on the few that matter, trusting the verdict because the evidence is on screen.

## Positioning

Scanners say "this package is vulnerable". ProveItReachable says "your code reaches the vulnerable function, here is the file:line call path, here are the advisory sources". When it can't tell, it says `uncertain` instead of guessing. The product's claims are bounded by its evidence and its measured eval precision.

## Operating Context

- One screen: pick a demo repo (cached replay, zero cost) or paste a public GitHub URL (live scan, rate-limited, spend-capped).
- A step-by-step progress feed (resolve → OSV → research → analyze → reason → verdicts), then a ranked findings report on the same screen.
- Export as JSON or Markdown.
- Live scans take minutes; demo replays finish in about 20 seconds.

## Capabilities and Constraints

- Verdict labels are exact strings: `likely reachable`, `no reachable path detected`, `uncertain`.
- Each finding carries confidence (high / medium / low), limitations, advisory links (exact source URLs), source locations (`file:line`); `likely reachable` also carries a call path.
- Ranking: `likely reachable`, then `uncertain`, then `no reachable path detected`.
- Evidence comes before explanation. `uncertain` must be obvious and never signalled by color alone.
- Advisory and model text is untrusted and rendered as text only.
- States: idle, demo replay running, live scan running, step failures (finding becomes `uncertain`), clean (no vulnerable deps), unresolved dependencies list, input errors (invalid URL, private repo, over size cap, not Python), rate limited, spend cap reached (live paused, demos work).
- Optional and cuttable: call-chain proof ("proven" / "not proven") for `likely reachable` findings.
- Only public repos; no accounts; no auto-fix; no exploit generation.

## Brand Commitments

- Name: **ProveItReachable**. No logo or brand assets exist; the name set in type, with a small drawn call-path glyph, is the identity. Third-party names (Nebius Token Factory, Nemotron, Tavily) appear as plain text only, never as imitated logos.
- Look: a calm developer-tool product in the spirit of Linear, Vercel or Socket.dev (no copied branding). Clean, simple, restrained colour, generous whitespace. One sans with a mono for data.
- Voice: precise and evidence-led, in plain developer words ("Scan repo", "Findings", "Call path"). Never overclaims; says what was and wasn't examined.
- Visible by design: the agent pipeline (each stage names its tool) and Tavily's contribution on each finding's sources, so a cold evaluator sees how the result was made.

## Evidence on Hand

- No measured results yet. The "41 alerts → 3 reachable" line is a motivating example only and must be labeled as such until the eval measures a real number.
- Demo replays will be recorded from real M1 runs on 3 public repos. Nothing else (testimonials, user counts, benchmarks) exists and none may be invented.

## Product Principles

1. **Evidence first.** Show the call path and the sources before any explanation or score.
2. **Honest uncertainty.** `uncertain` is a first-class, visible answer, not a failure state to hide.
3. **Noise reduction is the value.** The headline is how many alerts actually matter ("N advisories to M likely reachable").
4. **Zero-friction proof of value.** A cold visitor reaches a full report in one click and under 20 seconds.
5. **Show the work.** The pipeline and the tool behind each stage are visible while it runs and in the finished report.
6. **Claims match measurements.** No number appears unless the eval or the scan produced it, and it is labelled with its origin.
7. **Product, not document.** Clean and quiet: restrained colour reserved for verdicts, plain words, nothing decorative.

## Accessibility & Inclusion

- WCAG 2.2 AA: contrast, keyboard operation, visible focus, screen-reader labels, status announcements for the progress feed.
- Verdicts are distinguished by text label and shape or pattern, never color alone.
- Desktop-first (judges on laptops); fully usable on mobile widths.
- Single light theme; dark mode is not required.
