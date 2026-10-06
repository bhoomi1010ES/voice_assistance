"""Paired synthetic foreground measurements through the existing LLM service."""

import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path

from app.core.config import Settings
from app.llm.service import LLMService
from app.llm.types import LLMMessage, LLMRequest, LLMRole
from app.planning.evaluation import (
    DEFAULT_CORPUS,
    OWNER,
    _percentile,
    fixture_snapshot,
    implementation_fingerprint,
)
from app.planning.extraction import extract
from app.planning.types import EXTRACTOR_VERSION


async def measure(llm, settings, corpus, *, pair_count=10):
    cases = [
        case for case in corpus["cases"] if case["id"] in {"concrete_intention", "supplied_xyz"}
    ]

    async def foreground():
        request = LLMRequest(
            session_id=uuid.uuid4(),
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
            system_instructions="Reply with the single word READY.",
            messages=(LLMMessage(role=LLMRole.USER, content="Ready?"),),
            allowed_tools=(),
            tool_choice="none",
            max_output_tokens=64,
        )
        started = time.perf_counter()
        status = "failed"
        try:
            async with asyncio.timeout(settings.plan_extraction_timeout_ms / 1000):
                async for event in llm.stream(request):
                    if event.event_type == "response_completed":
                        status = "completed"
        except TimeoutError:
            status = "timeout"
        except Exception:  # noqa: BLE001 - never emit raw provider/validation errors
            pass
        return {"status": status, "duration_ms": round((time.perf_counter() - started) * 1000, 3)}

    async def background(case):
        started = time.perf_counter()
        status = "failed"
        try:
            await extract(
                llm,
                fixture_snapshot(case),
                uuid.uuid4(),
                case["turns"][0]["transcript"],
                timeout_ms=settings.plan_extraction_timeout_ms,
                max_actions=settings.plan_max_actions_per_turn,
                max_tokens=settings.llm_max_output_tokens,
            )
            status = "completed"
        except Exception:  # noqa: BLE001 - candidate quality is reported by the corpus evaluator
            pass
        return {"status": status, "duration_ms": round((time.perf_counter() - started) * 1000, 3)}

    pairs = []
    for index in range(pair_count):
        case = cases[index % len(cases)]

        async def with_background(case=case):
            started = time.perf_counter()
            task = asyncio.create_task(background(case)) if llm.background_capacity > 0 else None
            scheduling_ms = (time.perf_counter() - started) * 1000
            await asyncio.sleep(0)
            observed = await foreground()
            result = await task if task else {"status": "capacity_skip"}
            return observed, task is not None, scheduling_ms, result

        if index % 2:
            observed, scheduled, scheduling_ms, background_result = await with_background()
            baseline = await foreground()
        else:
            baseline = await foreground()
            observed, scheduled, scheduling_ms, background_result = await with_background()
        added = (
            observed["duration_ms"] - baseline["duration_ms"]
            if (observed["status"] == baseline["status"] == "completed")
            else None
        )
        pairs.append(
            {
                "baseline": baseline,
                "with_background": observed,
                "background_scheduled": scheduled,
                "background": background_result,
                "order": "background_first" if index % 2 else "baseline_first",
                "scheduling_ms": round(scheduling_ms, 3),
                "added_latency_ms": added,
            }
        )
    valid = [pair["added_latency_ms"] for pair in pairs if pair["added_latency_ms"] is not None]
    return {
        "evaluation_kind": "live_paired_foreground_latency",
        "extractor_version": EXTRACTOR_VERSION,
        "implementation_sha256": implementation_fingerprint(),
        "runtime_timeout_ms": settings.plan_extraction_timeout_ms,
        "pairs": pairs,
        "valid_pairs": len(valid),
        "median_added_latency_ms": statistics.median(valid) if valid else None,
        "p95_added_latency_ms": _percentile(valid, 0.95),
        "limitations": (
            "Counterbalanced synthetic pairs with variable provider/network latency; "
            "no production latency guarantee."
        ),
    }


async def main(args):
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
    try:
        await llm.initialize()
        result = await measure(
            llm,
            settings,
            json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8")),
            pair_count=args.pairs,
        )
        if llm.provider_info:
            result["provider"] = llm.provider_info.provider
            result["configured_model"] = llm.provider_info.configured_model
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result))
    finally:
        await llm.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-ms", type=int, default=3000)
    parser.add_argument("--pairs", type=int, choices=range(2, 31), default=10)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
