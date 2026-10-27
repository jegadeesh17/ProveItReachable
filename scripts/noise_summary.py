"""Empirically measure security alert noise reduction across ProveItReachable demo replays and evaluation results.

Calculates:
- Total advisory alerts flagged by traditional SCA / manifest scanners (every CVE entry)
- Likely reachable findings identified by ProveItReachable's AST + Nemotron engine
- Unreachable findings (proven safe / false alarms eliminated)
- Uncertain findings (honest abstentions)
- Call-chain proofs executed in the Token Factory Sandbox
"""

import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPLAYS_DIR = ROOT / "demo" / "replays"
EVAL_LATEST = ROOT / "eval" / "results" / "latest.json"


def summarize_replays() -> dict:
    total_findings = 0
    likely_reachable = 0
    not_reachable = 0
    uncertain = 0
    proven = 0
    repo_count = 0

    for path in sorted(glob.glob(str(REPLAYS_DIR / "*.json"))):
        if Path(path).name == "index.json":
            continue
        repo_count += 1
        data = json.load(open(path, encoding="utf-8"))
        findings = data.get("report", {}).get("findings", [])
        total_findings += len(findings)
        for f in findings:
            verdict = f.get("verdict")
            if verdict == "likely reachable":
                likely_reachable += 1
            elif verdict == "no reachable path detected":
                not_reachable += 1
            elif verdict == "uncertain":
                uncertain += 1

            proof = f.get("proof") or {}
            if proof.get("status") == "proven":
                proven += 1

    return {
        "repos": repo_count,
        "total_advisories": total_findings,
        "likely_reachable": likely_reachable,
        "not_reachable": not_reachable,
        "uncertain": uncertain,
        "proven": proven,
        "noise_reduction_pct": (
            (1.0 - (likely_reachable / total_findings)) * 100 if total_findings else 0.0
        ),
    }


def main():
    stats = summarize_replays()
    print("=" * 60)
    print("PROVEITREACHABLE: EMPIRICAL NOISE REDUCTION SUMMARY")
    print("=" * 60)
    print(f"Demo Repositories Analyzed: {stats['repos']}")
    print(f"Total Traditional Scanner Alerts: {stats['total_advisories']}")
    print(f"  -> Likely Reachable (Actionable): {stats['likely_reachable']}")
    print(f"  -> No Reachable Path (Safe/Dismissed): {stats['not_reachable']}")
    print(f"  -> Uncertain (Honest Abstention): {stats['uncertain']}")
    print(f"  -> Proven in Token Factory Sandbox: {stats['proven']}")
    print(f"Measured Noise Reduction: {stats['noise_reduction_pct']:.1f}%")
    print("-" * 60)

    if EVAL_LATEST.exists():
        eval_data = json.load(open(EVAL_LATEST, encoding="utf-8"))
        print(f"Evaluation Benchmark Cases: {eval_data.get('n', 0)}")
        print(f"  -> Precision: {eval_data.get('precision', 0):.2f}")
        print(f"  -> Recall: {eval_data.get('recall', 0):.2f}")
        print(f"  -> Abstention Rate: {eval_data.get('abstention_rate', 0):.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()

