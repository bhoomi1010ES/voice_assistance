"""Synthetic label replay or live selected-provider evaluation; no database writes.

Run: python -m app.planning.evaluation --live --output docs/pm2_live.json
Replay validates labeled proposals, and can never establish model quality.
Reports contain only fixture IDs, versions, reason/outcome counts and timings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
import uuid
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.llm.service import LLMService
from app.planning.extraction import ExtractionError, extract
from app.planning.policy import PlanningConsent
from app.planning.resolution import _words, validate
from app.planning.service import recognize_mode_control, recognize_plan_selection
from app.planning.types import EXTRACTOR_VERSION, Envelope, PlanningSnapshot, Proposal, Span, Target

DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "tests/fixtures/planning/conversations.json"
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000101")


def implementation_fingerprint():
    """Bind measurements to source bytes without exposing local configuration."""
    app = Path(__file__).resolve().parents[1]
    paths = (
        "planning/extraction.py",
        "planning/wire.py",
        "planning/resolution.py",
        "planning/types.py",
        "planning/observer.py",
        "planning/repository.py",
        "planning/policy.py",
        "planning/service.py",
        "planning/evaluation.py",
        "llm/service.py",
        "llm/types.py",
        "llm/providers/openai_responses.py",
    )
    return {path: hashlib.sha256((app / path).read_bytes()).hexdigest() for path in paths}


def _id(text):
    return uuid.uuid5(uuid.NAMESPACE_URL, "planning-fixture:" + text)


def fixture_snapshot(case):
    initial = case["initial"]
    active = _id(case["id"] + ":plan:" + initial["active_plan"]) if initial["active_plan"] else None
    plans = []
    targets = []
    if active:
        plans.append(Target(active, "plan", initial["active_plan"], active, 1))
    for target in initial["owned_targets"]:
        plan_id = active if target.get("plan") == initial["active_plan"] else None
        item = Target(
            _id(case["id"] + target["ref"]),
            target["kind"],
            target["title"],
            plan_id,
            target["revision"],
        )
        (plans if target["kind"] == "plan" else targets).append(item)
    return PlanningSnapshot(
        PlanningConsent(
            OWNER,
            _id(case["id"]),
            True,
            True,
            initial["mode"],
            2,
            2,
            memory_excluded=initial["memory_excluded"],
        ),
        datetime.fromisoformat(case["clock"]),
        case["timezone"],
        active,
        tuple(plans),
        tuple(targets),
    )


def label_proposal(label):
    operation = label["operation"]
    classification = (
        "INFORMATION"
        if operation == "ADD_PLAN_CONTEXT"
        else "PROJECT"
        if operation == "CREATE_PLAN"
        else "PROGRESS"
        if operation == "COMPLETE_TASK"
        else "CORRECTION"
        if operation.startswith("UPDATE_")
        else "REMINDER"
        if "REMINDER" in operation
        else "COMMITMENT"
    )
    return Proposal(
        operation=operation,
        classification=classification,
        title=label["title"],
        actor=label["actor"],
        source=Span(**label["source"]),
        temporal=Span(**label["temporal_source"]) if "temporal_source" in label else None,
        recurrence=label.get("recurrence"),
        recurrence_source=Span(**label["temporal_source"]) if label.get("recurrence") else None,
    )


def _matches(decision, label):
    if decision.proposal.operation != label["operation"]:
        return False
    source = decision.proposal.source
    expected = label["source"]
    overlap = min(source.start + source.length, expected["start"] + expected["length"]) - max(
        source.start, expected["start"]
    )
    if overlap < min(source.length, expected["length"]) / 2:
        return False
    if decision.proposal.operation == "ADD_PLAN_CONTEXT":
        return True
    verbs = {
        "finish",
        "prepare",
        "complete",
        "start",
        "write",
        "take",
        "deliver",
        "send",
        "call",
        "ready",
        "running",
        "initial",
    }
    actual_core = _words(decision.proposal.title) - verbs
    expected_core = _words(label["title"]) - verbs
    return bool(actual_core and actual_core <= expected_core)


def _percentile(values, fraction):
    if not values:
        return None
    return sorted(values)[min(len(values) - 1, int((len(values) - 1) * fraction))]


def summarize(rows, *, kind):
    totals = Counter()
    durations = []
    for row in rows:
        totals.update(row["counts"])
        if row["requested"]:
            durations.append(row["duration_ms"])
    auto = totals["proposed_auto"]
    expected = totals["expected_auto"]
    precision = totals["correct_auto"] / auto if auto else None
    recall = totals["correct_auto"] / expected if expected else None
    requests = totals["requests"]
    return {
        "counts": dict(totals),
        "candidate_precision": precision,
        "candidate_recall": recall,
        "false_actions": auto - totals["correct_auto"],
        "target_ambiguity": {
            "expected": totals["expected_ambiguity"],
            "correct": totals["correct_ambiguity"],
        },
        "duplicate_decisions": {
            "expected": totals["expected_duplicates"],
            "correct": totals["correct_duplicates"],
        },
        "timeout_rate": totals["timeouts"] / requests if requests else None,
        "extraction_latency_ms": {
            "median": statistics.median(durations) if durations else None,
            "p95": _percentile(durations, 0.95),
            "max": max(durations) if durations else None,
        },
        "added_foreground_latency_ms": None,
        "latency_note": (
            "Background duration measured; shared-provider foreground contention "
            "requires paired runtime measurements."
        ),
        "quality_gate_met": bool(
            kind == "live_provider"
            and precision is not None
            and precision >= 0.95
            and recall is not None
            and recall >= 0.90
            and auto == totals["correct_auto"]
            and not totals["temporal_errors"]
            and not totals["failures"]
            and totals["correct_ambiguity"] == totals["expected_ambiguity"]
            and totals["correct_duplicates"] == totals["expected_duplicates"]
            and totals["correct_batch_clarifications"] == totals["expected_batch_clarifications"]
        ),
    }


async def evaluate(corpus, *, llm=None, settings=None, split="all"):
    started_at = datetime.now(UTC).isoformat()
    rows = []
    for case in corpus["cases"]:
        if split != "all" and case["split"] != split:
            continue
        snapshot = fixture_snapshot(case)
        cache = {}
        for turn in case["turns"]:
            started = time.perf_counter()
            labels = turn["expected_actions"]
            counts = Counter(
                expected_auto=sum(label["disposition"] == "AUTO" for label in labels),
                expected_ambiguity=sum(
                    label.get("reason") == "ambiguous_target" for label in labels
                ),
                expected_duplicates=sum(label.get("reason") == "duplicate" for label in labels),
                expected_batch_clarifications=int(turn.get("batch_outcome") == "CLARIFY"),
            )
            text = turn["transcript"]
            eligible = (
                snapshot.consent.mode == "plan"
                and not snapshot.consent.memory_excluded
                and not recognize_mode_control(text)
                and recognize_plan_selection(text) is None
            )
            status, reason, resolved, requested = "skipped", "ineligible", (), False
            if "retry_of" in turn:
                status, reason = "replayed", "original_clock_retained"
                assert turn["retry_of"] in cache
            elif eligible:
                try:
                    if llm:
                        requested = True
                        counts["requests"] += 1
                        envelope = await extract(
                            llm,
                            snapshot,
                            _id(case["id"] + turn["id"]),
                            text,
                            timeout_ms=settings.plan_extraction_timeout_ms,
                            max_actions=settings.plan_max_actions_per_turn,
                            max_tokens=settings.llm_max_output_tokens,
                        )
                    else:
                        envelope = Envelope(actions=[label_proposal(label) for label in labels])
                    resolved = validate(
                        envelope,
                        text,
                        snapshot,
                        max_actions=settings.plan_max_actions_per_turn if settings else 8,
                    )
                    status, reason = "validated", ""
                    if (
                        turn.get("batch_outcome") == "CLARIFY"
                        and resolved
                        and all(
                            d.disposition == "CLARIFY" and d.reason == "action_overflow"
                            for d in resolved
                        )
                    ):
                        counts["correct_batch_clarifications"] += 1
                except TimeoutError:
                    status, reason = "failed", "timeout"
                    counts["timeouts"] += 1
                    counts["failures"] += 1
                except ExtractionError as error:
                    reason = str(error)
                    if reason == "action_overflow" and turn.get("batch_outcome") == "CLARIFY":
                        status = "clarified"
                        counts["correct_batch_clarifications"] += 1
                    else:
                        status = "failed"
                        counts["failures"] += 1
                except Exception:
                    status, reason = "failed", "provider_unavailable"
                    counts["failures"] += 1
            cache[turn["id"]] = resolved
            remaining = list(labels)
            for decision in resolved:
                if decision.disposition == "AUTO":
                    counts["proposed_auto"] += 1
                label = next((item for item in remaining if _matches(decision, item)), None)
                if label is None:
                    continue
                remaining.remove(label)
                same = decision.disposition == label["disposition"]
                if label.get("local_at") and decision.disposition == "AUTO":
                    expected = (
                        datetime.fromisoformat(label["local_at"])
                        .replace(tzinfo=ZoneInfo(case["timezone"]))
                        .astimezone(UTC)
                    )
                    if expected != decision.scheduled_at:
                        same = False
                        counts["temporal_errors"] += 1
                if same and decision.disposition == "AUTO":
                    counts["correct_auto"] += 1
                if (
                    same
                    and label.get("reason") == "ambiguous_target"
                    and decision.reason == "ambiguous_target"
                ):
                    counts["correct_ambiguity"] += 1
                if same and label.get("reason") == "duplicate" and decision.reason == "duplicate":
                    counts["correct_duplicates"] += 1
                counts["correct_dispositions"] += same
            # Ephemeral prior receipts simulate only corpus continuity, never DB writes.
            for ordinal, decision in enumerate(resolved):
                if decision.disposition == "AUTO" and decision.proposal.operation in {
                    "CREATE_TASK",
                    "CREATE_REMINDER",
                }:
                    target = Target(
                        _id(case["id"] + turn["id"] + str(ordinal)),
                        "task" if decision.proposal.operation == "CREATE_TASK" else "reminder",
                        decision.proposal.title,
                        decision.plan_id,
                        1,
                        decision.scheduled_at,
                        decision.proposal.recurrence,
                    )
                    snapshot = replace(snapshot, targets=(*snapshot.targets, target))
            rows.append(
                {
                    "case_id": case["id"],
                    "turn_id": turn["id"],
                    "split": case["split"],
                    "status": status,
                    "reason": reason,
                    "requested": requested,
                    "counts": dict(counts),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "outcomes": dict(Counter(d.disposition for d in resolved)),
                    "validation_reasons": dict(Counter(d.reason for d in resolved)),
                }
            )
    kind = "live_provider" if llm else "labeled_proposal_replay"
    overall = summarize(rows, kind=kind)
    splits = {
        name: summarize([r for r in rows if r["split"] == name], kind=kind)
        for name in ("development", "held_out")
    }
    return {
        "evaluation_kind": kind,
        "started_at_utc": started_at,
        "evaluated_split": split,
        "runtime_timeout_ms": settings.plan_extraction_timeout_ms if settings else None,
        "extractor_version": EXTRACTOR_VERSION,
        "implementation_sha256": implementation_fingerprint(),
        "corpus_sha256": hashlib.sha256(
            json.dumps(corpus, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
        "policy_version": corpus["policy_version"],
        "corpus_version": corpus["schema_version"],
        "matching_policy": (
            "Same operation, overlapping evidence, normalized title core, exact expected times; "
            "one-to-one matching. Concise titles allowed; combined deliverables rejected."
        ),
        "overall": overall,
        "splits": splits,
        "full_corpus_gate_met": bool(
            split == "all"
            and overall["quality_gate_met"]
            and all(summary["quality_gate_met"] for summary in splits.values())
        ),
        "observations": rows,
        "limitations": (
            "Small synthetic corpus. Replay is not model precision. No foreground latency "
            "guarantee or live database/native acceptance."
        ),
    }


async def _main(args):
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    llm = None
    settings = None
    try:
        if args.live:
            # Only synthetic fixture text goes to the existing selected provider.
            # Disable unrelated memory/OKF shadow capabilities in this evaluator
            # process; local dotenv and application deployment are unchanged.
            settings = Settings(
                memory_retrieval_mode="off",
                memory_write_enabled=False,
                okf_shadow_reads=False,
                plan_mode_enabled=True,
                plan_extraction_mode="shadow",
                plan_auto_actions_enabled=False,
                plan_test_user_ids=(OWNER,),
                plan_extraction_timeout_ms=args.timeout_ms,
            )
            llm = LLMService(settings)
            await llm.initialize()
        result = await evaluate(corpus, llm=llm, settings=settings, split=args.split)
        if llm and llm.provider_info:
            result["provider"] = llm.provider_info.provider
            result["configured_model"] = llm.provider_info.configured_model
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "evaluation_kind": result["evaluation_kind"],
                    "runtime_timeout_ms": result["runtime_timeout_ms"],
                    "full_corpus_gate_met": result["full_corpus_gate_met"],
                    "overall": result["overall"],
                }
            )
        )
    finally:
        if llm:
            await llm.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--split", choices=("all", "development", "held_out"), default="all")
    parser.add_argument("--timeout-ms", type=int, default=3000)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(_main(parser.parse_args()))
