# Project documentation

Living design, architecture, review, and operational documentation for ProveItReachable.

| Document | Purpose | Written by | Updated when |
|---|---|---|---|
| [SPEC.md](SPEC.md) | User journeys, acceptance criteria, and system requirements | ProductAnalyst | Frozen at Phase 2 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System components, safety boundaries, contracts, test commands | SoftwareArchitect | Frozen Phase 2, amended through ADRs |
| [DECISIONS.md](DECISIONS.md) | Architectural Decision Records (ADR-0001 through ADR-0045) | SoftwareArchitect | Each architectural change |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Google Cloud Run deploy runbook, budget profile, kill switch, and verification | PlatformEngineer | Each deploy revision and budget adjustment |
| [PLATFORM_FEEDBACK.md](PLATFORM_FEEDBACK.md) | Developer feedback on Nebius Token Factory, Sandboxes, Nemotron, and LangSmith | Orchestrator & subagents | As platform facts and issues are discovered |
| [WALKTHROUGH.md](WALKTHROUGH.md) | Plain-English user walkthrough of how the system works and is built | Orchestrator | Phase 2.4 setup |
| [FEEDBACK.md](FEEDBACK.md) | User review feedback from prototype rounds and milestone UAT sessions | Orchestrator | After each user feedback round |
| [PROJECT_MENTAL_MODEL.md](PROJECT_MENTAL_MODEL.md) | Mental model, scope boundaries, personas, and constraints | Orchestrator | Phase 0 scoping and planning |
| [TASKS.json](TASKS.json) | Granular ordered task graph across milestones M1 and M2 | TaskPlanner | Initialized Phase 2.3, tracked through build |
| [QA_RESULTS.json](QA_RESULTS.json) | Automated test execution audit trail and verification records | QaTester / Developer | After each verified task |
| [ADVERSARIAL_REVIEW.md](ADVERSARIAL_REVIEW.md) | Milestone adversarial audits for contract drift, edge cases, and safety | AdversarialReviewer | End of each milestone |
| [SECURITY_REVIEW.md](SECURITY_REVIEW.md) | Security reviews covering untrusted code isolation, egress boundaries, and credentials | SecurityReviewer | Design review and milestone reviews |
| [UI_REVIEW.md](UI_REVIEW.md) | Visual design, responsive layout, and accessibility audits | UiReviewer | Milestone UI reviews |
