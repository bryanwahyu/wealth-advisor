"""CLI entry point.

    python -m wealth_advisor run C001
    python -m wealth_advisor run --all --review auto
    python -m wealth_advisor run C001 --simulate-crm down --memory scylla
    python -m wealth_advisor history C001
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from .app import build_system
from .config import Settings
from .hitl import AutoApproveReviewer, DeferReviewer, FileReviewer, InteractiveReviewer, Reviewer
from .memory.long_term import build_long_term_memory
from .observability import configure_logging
from .report import write_report


def _reviewer(args: argparse.Namespace) -> Reviewer:
    if args.review == "auto":
        return AutoApproveReviewer()
    if args.review == "defer":
        return DeferReviewer()
    if args.review == "file":
        if not args.review_file:
            sys.exit("--review file needs --review-file PATH")
        return FileReviewer(Path(args.review_file))
    return InteractiveReviewer()


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings()
    if args.data_dir:
        s.data_dir = Path(args.data_dir)
    if args.output_dir:
        s.output_dir = Path(args.output_dir)
        s.memory_db = s.output_dir / "memory.sqlite3"
    s.as_of = getattr(args, "as_of", None)
    s.crm_failure_mode = getattr(args, "simulate_crm", "none")
    s.market_failure_mode = getattr(args, "simulate_market", "none")
    s.use_llm = getattr(args, "llm", False)
    return s


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    batch_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
    log_path = configure_logging(settings.log_dir, batch_id, verbose=args.verbose)

    client_ids = args.client_ids
    if args.all:
        client_ids = sorted(p.stem for p in (settings.data_dir / "clients").glob("*.json"))
    if not client_ids:
        sys.exit("give at least one CLIENT_ID or --all")

    system = build_system(settings, _reviewer(args), memory_backend=args.memory)
    exit_code = 0
    try:
        for cid in client_ids:
            report = system.orchestrator.run(cid)
            jp, mp = write_report(report, settings.output_dir)
            score = report["risk"]["score"]
            print(
                f"{cid:<6} {report['status']:<24} risk={'-' if score is None else score:<4} "
                f"findings={len(report['findings']):<3} recs={len(report['recommendations']):<3} -> {mp}"
            )
            if args.json:
                print(json.dumps(report, indent=2, default=str))
            if report["status"] == "failed":
                exit_code = 1
    finally:
        system.close()
    print(f"log: {log_path}")
    return exit_code


def cmd_history(args: argparse.Namespace) -> int:
    settings = _settings(args)
    configure_logging(None, "history", verbose=False)
    memory = build_long_term_memory(args.memory, settings.memory_db)
    try:
        runs = memory.latest_runs(args.client_id, limit=args.limit)
    except Exception as exc:
        print(f"history unavailable: {exc}", file=sys.stderr)
        return 1
    finally:
        memory.close()
    if not runs:
        print(f"no stored runs for {args.client_id} in {memory.backend}")
    for r in runs:
        ts = datetime.fromtimestamp(r.created_at / 1000).isoformat(timespec="seconds")
        print(f"{ts}  {r.run_id}  {r.status:<24} risk={r.risk_score:<4} {', '.join(r.finding_codes)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="wealth_advisor", description="Wealth Advisor multi-agent assistant")
    p.add_argument("--data-dir")
    p.add_argument("--output-dir")
    p.add_argument("--memory", choices=["sqlite", "scylla", "none"], default="sqlite",
                   help="long-term memory backend (scylla reads SCYLLA_* env vars)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="analyze one or more clients")
    r.add_argument("client_ids", nargs="*")
    r.add_argument("--all", action="store_true", help="run every client in data/clients")
    r.add_argument("--review", choices=["interactive", "auto", "defer", "file"], default="interactive")
    r.add_argument("--review-file", help="JSON decisions for --review file")
    r.add_argument("--simulate-crm", choices=["none", "flaky", "down", "slow"], default="none")
    r.add_argument("--simulate-market", choices=["none", "down"], default="none")
    r.add_argument("--as-of", help="analysis date (YYYY-MM-DD); default: latest transaction date")
    r.add_argument("--llm", action="store_true", help="write the briefing with Claude (needs ANTHROPIC_API_KEY)")
    r.add_argument("--json", action="store_true", help="also print the JSON report")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(func=cmd_run)

    h = sub.add_parser("history", help="show stored runs for a client")
    h.add_argument("client_id")
    h.add_argument("--limit", type=int, default=10)
    h.set_defaults(func=cmd_history)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
