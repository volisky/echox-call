"""Rule-only recomputation for completed postcall jobs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from echox_call.core.db import DatabaseConnectionError
from echox_call.core.settings import (
    DatabaseConfigError,
    PostcallWorkerConfigError,
    load_postcall_worker_settings,
)
from echox_call.features.audio_analysis.postcall.attention_rules import (
    PostcallAttentionRuleError,
    load_attention_rules,
)
from echox_call.features.audio_analysis.postcall.repository import (
    PostcallJobRepository,
    PostcallResultContractError,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recompute postcall attention insights from saved timeline segments"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--job-id", help="Recompute one completed job by external jobId")
    target.add_argument(
        "--all-completed",
        action="store_true",
        help="Recompute all completed jobs that already have saved timeline segments",
    )
    parser.add_argument(
        "--rules-path",
        type=Path,
        help="Override POSTCALL_ATTENTION_RULES_PATH",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate and print the summary without updating the database",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_postcall_worker_settings()
        rules_path = args.rules_path or settings.attention_rules_path
        rules = load_attention_rules(rules_path)
        repository = PostcallJobRepository()
        rows = repository.list_completed_jobs_for_attention_recompute(
            job_id=args.job_id if args.job_id else None,
        )
        if args.job_id and not rows:
            print(
                json.dumps(
                    {
                        "jobId": args.job_id,
                        "updated": False,
                        "error": "completed postcall job not found",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1

        summaries: list[dict[str, object]] = []
        for row in rows:
            timeline = repository.get_timeline_payloads_for_attention_recompute(
                job_internal_id=row["id"],
            )
            evaluation = rules.evaluate(timeline)
            if args.dry_run:
                summaries.append(
                    {
                        "jobId": row["job_id"],
                        "jjdh": row["jjdh"],
                        "level": evaluation.level,
                        "levelName": evaluation.level_name,
                        "reviewSegmentCount": len(evaluation.review_segments),
                        "attentionInsightCount": len(evaluation.attention_insights),
                        "matchedRuleCodes": evaluation.matched_rule_codes,
                        "ruleVersion": evaluation.rule_version,
                        "updated": False,
                    }
                )
                continue

            summary = repository.persist_recomputed_attention(
                job_row=row,
                timeline=timeline,
                attention_evaluation=evaluation,
            )
            summary["updated"] = True
            summaries.append(summary)

        print(
            json.dumps(
                {
                    "ruleVersion": rules.rule_version,
                    "dryRun": args.dry_run,
                    "jobCount": len(summaries),
                    "jobs": summaries,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (DatabaseConfigError, PostcallWorkerConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except DatabaseConnectionError as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 1
    except (PostcallAttentionRuleError, PostcallResultContractError) as exc:
        print(f"attention rules error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
