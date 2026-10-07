"""Conservative evidence, ownership, temporal and duplicate proposal validation."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.planning.policy import AUTOMATIC_OPERATION_FIELDS, CONFIRMATION_OPERATIONS
from app.planning.types import Decision, Envelope, PlanningSnapshot, Proposal, Span
from app.planning.wire import sentence_boundary_text
from app.services.device_time import timezone_for_request
from app.services.recurrence import RecurrenceResolutionError, validate_recurrence_rule
from app.services.task_due_dates import (
    TaskDueDateResolutionError,
    has_relative_duration_expression,
    has_temporal_expression,
    normalize_clock_expression,
    resolve_task_due_at,
)
from app.services.task_linked_reminders import has_real_clock

_NEGATIVE = re.compile(
    r"\b(?:not|don't|doesn't|didn't|never|no longer|might|maybe|thinking|considering|"
    r"if|would|could|should|suppose|hypothetically|last year|last month|last week|"
    r"ago|yesterday|used to)\b",
    re.I,
)
_REFERENCE = re.compile(
    r"\b(?:said|says|tutorial|document|sample|example|quote|according to)\b", re.I
)
_ACTION = re.compile(
    r"\b(?:i|we|our|me|let's|must|needs?|will|want|have|make|move|change|finish|finished|"
    r"complete|completed|remind|cancel|delete|archive|undo|reassign|send|book|mark|reschedule|postpone)\b",
    re.I,
)
_ANCHOR = re.compile(r"\b(?:after|before|prior to|ahead of)\b", re.I)
_TEMPORAL = re.compile(
    r"\b(?:after|before|next week|every|daily|weekly|monthly|at\s+\d|"
    r"in\s+\d+\s+(?:hours?|minutes?))\b",
    re.I,
)

_PRONOUNS = frozenset(
    {
        "that",
        "it",
        "this",
        "that task",
        "the task",
        "this task",
        "that reminder",
        "the reminder",
        "this reminder",
        "that one",
        "the one",
    }
)


def identity(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def _is_pronoun_reference(text: str) -> bool:
    cleaned = identity(text)
    if cleaned in _PRONOUNS:
        return True
    return cleaned in {
        "move that",
        "move it",
        "move this",
        "mark it",
        "mark that",
        "mark this",
        "mark it done",
        "mark that done",
        "mark this done",
        "mark it complete",
        "mark that complete",
        "mark this complete",
        "complete it",
        "complete that",
        "complete this",
        "finish it",
        "finish that",
        "finish this",
        "cancel it",
        "cancel that",
        "cancel this",
        "delete it",
        "delete that",
        "delete this",
        "reschedule it",
        "reschedule that",
        "reschedule this",
    }


def _plan_name(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def _recurrence_identity(rule: str | None) -> str | None:
    if not rule:
        return None
    try:
        return validate_recurrence_rule(rule)
    except RecurrenceResolutionError:
        return rule


def _words(text: str) -> set[str]:
    words = set()
    for word in re.findall(r"\w+", text.casefold()):
        if word in {"the", "a", "an", "to", "and", "of", "my", "our"}:
            continue
        word = {"prepared": "prepare", "preparing": "prepare", "starting": "start"}.get(word, word)
        if word.endswith("ed") and len(word) > 5:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 3:
            word = word[:-1]
        words.add(word)
    return words


def _task_title_matches(existing: str, proposed: str, source: str) -> bool:
    if identity(existing) == identity(proposed):
        return True
    existing_words, proposed_words = _words(existing), _words(proposed)
    # Permit concise/inflected titles only when the source supports BOTH titles.
    # Different work verbs cannot be silently merged through a shared noun.
    neutral = {"prepare", "finish", "complete", "write", "start"}
    core = existing_words - neutral
    return bool(
        core
        and core == proposed_words - neutral
        and existing_words <= _words(source)
        and proposed_words <= _words(source)
    )


def _reminder_title_matches(existing: str, proposed: str, source: str) -> bool:
    if identity(existing) == identity(proposed):
        return True
    existing_words, proposed_words = _words(existing), _words(proposed)
    return bool(
        existing_words and existing_words == proposed_words and existing_words <= _words(source)
    )


def _valid_span(span: Span, transcript: str) -> bool:
    return (
        transcript[span.start : span.start + span.length] == span.text
        and len(span.text) == span.length
    )


def _sentence(transcript: str, span: Span) -> str:
    # Inspect surrounding sentence so cropping cannot remove negation or attribution.
    boundaries = sentence_boundary_text(transcript)
    start = max(boundaries.rfind(mark, 0, span.start) for mark in ".!?\n") + 1
    end = span.start + span.length
    ends = [index for mark in ".!?\n" if (index := boundaries.find(mark, end)) >= 0]
    if boundaries[span.start : end].rstrip().endswith((".", "!", "?")):
        pass
    elif ends:
        end = min(ends) + 1
    else:
        end = len(transcript)
    return transcript[start:end]


def _quoted(transcript: str, span: Span) -> bool:
    return any(
        match.start() < span.start + span.length and span.start < match.end()
        for match in re.finditer(r'"[^"\n]*"|“[^”\n]*”|(?<!\w)\x27[^\x27\n]+\x27(?!\w)', transcript)
    )


def _temporal_affinity(proposal: Proposal) -> bool:
    """A broad source span cannot borrow a date from another action clause."""
    if proposal.temporal is None:
        return True
    text = proposal.source.text
    anchors = re.findall(
        r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"\d{4}-\d{2}-\d{2}|at\s+\d{1,2})\b",
        text,
        re.I,
    )
    if len(anchors) < 2:
        return True
    offset = proposal.temporal.start - proposal.source.start
    end = offset + proposal.temporal.length
    separators = list(
        re.finditer(r"[,;.!?\n]|\band\b(?=\s+(?:by|on|we|i)\b)", sentence_boundary_text(text), re.I)
    )
    start = max((match.end() for match in separators if match.end() <= offset), default=0)
    stop = min((match.start() for match in separators if match.start() >= end), default=len(text))
    return _words(proposal.title) <= _words(text[start:stop])


def resolve_time(
    proposal: Proposal, snapshot: PlanningSnapshot, *, include_clock: bool = False
) -> tuple[datetime | None, str, str] | tuple[datetime | None, str, str, bool]:
    expression = proposal.temporal.text if proposal.temporal else ""
    zone, _ = timezone_for_request(expression, device_timezone=snapshot.timezone)

    def _fail(r: str):
        if include_clock:
            return None, zone, r, False
        return None, zone, r

    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError:
        return _fail("invalid_timezone")
    if _ANCHOR.search(proposal.source.text) and not _ANCHOR.search(expression):
        return _fail("missing_temporal_modifier")
    if re.search(r"\bnext week\b", proposal.source.text, re.I) and not re.search(
        r"\bnext week\b", expression, re.I
    ):
        return _fail("missing_temporal_modifier")
    if re.search(r"\bevery\b|\bdaily\b|\bweekly\b", proposal.source.text, re.I) and not re.search(
        r"\bevery\b|\bdaily\b|\bweekly\b", expression, re.I
    ):
        return _fail("missing_temporal_modifier")
    if not expression:
        if has_temporal_expression(proposal.source.text) or _TEMPORAL.search(proposal.source.text):
            return _fail("missing_temporal_evidence")
        if "REMINDER" in proposal.operation:
            return _fail("missing_time")
        return _fail("")
    relative_duration = has_relative_duration_expression(expression)
    if _ANCHOR.search(expression) and not relative_duration:
        reason = (
            "unsupported_offset"
            if re.search(r"\b(?:days?|hours?|minutes?)\b", expression, re.I)
            else "unsupported_anchor"
        )
        return _fail(reason)
    if re.search(r"\bnext week\b", expression, re.I):
        return _fail("date_range")
    if re.search(r"\b(?:monthly|month|business day)\b", expression, re.I):
        return _fail("unsupported_recurrence")
    if proposal.recurrence:
        try:
            normalized = validate_recurrence_rule(proposal.recurrence)
        except RecurrenceResolutionError:
            return _fail("unsupported_recurrence")
        evidence = proposal.recurrence_source.text if proposal.recurrence_source else ""
        daily = re.search(r"\bevery day\b|\bdaily\b", evidence, re.I)
        weekly = re.search(
            r"\bevery (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", evidence, re.I
        )
        weekdays = {
            "monday": "MO",
            "tuesday": "TU",
            "wednesday": "WE",
            "thursday": "TH",
            "friday": "FR",
            "saturday": "SA",
            "sunday": "SU",
        }
        expected = (
            "FREQ=DAILY"
            if daily
            else ("FREQ=WEEKLY;BYDAY=" + weekdays[weekly[1].casefold()] if weekly else None)
        )
        if normalized != expected:
            return _fail("ungrounded_recurrence")
        expression = re.sub(
            r"\bevery day\b|\bdaily\b|\bevery\s+", "", expression, flags=re.I
        ).strip()
    elif re.search(r"\bevery\b|\bdaily\b|\bweekly\b", expression, re.I):
        return _fail("unsupported_recurrence")
    # Never let a resolver discard an unknown modifier around a recognized date.
    grammar = (
        r"(?:today|tomorrow|day after tomorrow|(?:next )?"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
        r"\d{4}-\d{2}-\d{2}|(?:january|february|march|april|may|june|july|august|"
        r"september|october|november|december) "
        r"\d{1,2}(?:,? \d{4})?)?"
        r"(?P<clock>\s*(?:at|by)?\s*\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)?"
    )
    cleaned = expression.casefold().strip()
    # Explicit IANA zones/known spoken aliases are resolved separately.
    cleaned = re.sub(r"\b[A-Za-z]+/[A-Za-z_]+(?:/[A-Za-z_]+)?\b", "", expression).casefold().strip()
    cleaned = re.sub(r"^(?:by|on)\s+", "", cleaned)
    cleaned = normalize_clock_expression(cleaned)
    if not cleaned:
        return _fail("missing_time")
    matched = re.fullmatch(grammar, cleaned, re.I)
    duration = has_relative_duration_expression(cleaned)
    if not matched and not duration:
        return _fail("unsupported_temporal_expression")
    clock_text = (matched["clock"] or "").strip() if matched else ""
    clock_match = re.search(r"\d{1,2}", clock_text)
    has_clock = bool(clock_text or duration)
    if clock_match and 1 <= int(clock_match[0]) <= 12 and not re.search(r"[ap]\.?m\.?", clock_text):
        return _fail("ambiguous_time")
    if "REMINDER" in proposal.operation and not has_clock:
        return _fail("missing_time")
    if (
        clock_text
        and not re.match(r"(?:at|by)\b", clock_text)
        and not re.search(r"[ap]\.?m\.?", clock_text)
    ):
        cleaned = cleaned[: len(cleaned) - len(clock_text)] + "at " + clock_text
    if not has_clock:
        # Supply the frozen deadline time before weekday selection; the legacy
        # date-only weekday resolver otherwise advances same-day dates a week.
        cleaned += " at 23:59"
    try:
        at = resolve_task_due_at(
            due_at=None,
            due_expression=cleaned,
            source_transcript=cleaned,
            now_utc=snapshot.now_utc,
            timezone_name=zone,
        )
        if at is not None and not has_real_clock(at, zone):
            has_clock = False
        if include_clock:
            return at, zone, "", has_clock
        return at, zone, ""
    except (TaskDueDateResolutionError, ValueError) as error:
        message = str(error)
        reason = "unsupported_temporal_expression"
        if "nonexistent" in message:
            reason = "nonexistent_local_time"
        elif "ambiguous" in message:
            reason = "ambiguous_local_time"
        elif "future" in message:
            reason = "past_time"
        return _fail(reason)


def validate(
    envelope: Envelope, transcript: str, snapshot: PlanningSnapshot, *, max_actions: int = 8
) -> tuple[Decision, ...]:
    decisions: list[Decision] = []
    seen: set[tuple] = set()
    # Resource grouping follows the conversation, not the model's array order.
    for proposal in sorted(envelope.actions, key=lambda action: action.source.start):
        decision = Decision(
            proposal,
            "AUTO",
            "grounded",
            plan_id=None if proposal.operation == "CREATE_PLAN" else snapshot.active_plan_id,
        )

        spans = [s for s in (proposal.source, proposal.temporal, proposal.recurrence_source) if s]
        if any(not _valid_span(s, transcript) for s in spans) or any(
            s.start < proposal.source.start
            or s.start + s.length > proposal.source.start + proposal.source.length
            for s in spans[1:]
        ):
            decisions.append(decision.outcome("DENY", "invalid_evidence"))
            continue
        sentence = _sentence(transcript, proposal.source)
        if not _temporal_affinity(proposal):
            decisions.append(decision.outcome("DENY", "temporal_action_mismatch"))
            continue
        if (
            _NEGATIVE.search(sentence)
            or _REFERENCE.search(sentence)
            or "?" in sentence
            or _quoted(transcript, proposal.source)
        ):
            decisions.append(decision.outcome("NO_ACTION", "non_actionable_evidence"))
            continue
        if proposal.actor == "third_party" or proposal.classification in {
            "NEGATIVE",
            "REFERENCE",
            "QUESTION",
            "HYPOTHETICAL",
        }:
            decisions.append(decision.outcome("NO_ACTION", "non_actionable_attribution"))
            continue
        grounded = bool(_words(proposal.title)) and _words(proposal.title) <= _words(
            proposal.source.text
        )
        if not grounded or proposal.content and proposal.content not in proposal.source.text:
            decisions.append(decision.outcome("DENY", "ungrounded_action"))
            continue
        if (
            proposal.operation not in AUTOMATIC_OPERATION_FIELDS
            and proposal.operation not in CONFIRMATION_OPERATIONS
        ):
            decisions.append(decision.outcome("DENY", "unsupported_operation"))
            continue
        if re.match(
            r"\s*(?:send|email|book|buy|purchase|invite|share)\b", proposal.source.text, re.I
        ):
            decisions.append(decision.outcome("DENY", "unsupported_external_operation"))
            continue
        if proposal.operation == "CREATE_PLAN" and not re.search(
            r"\b(?:start(?:ing)?|launch(?:ing)?|begin(?:ning)?)\s+(?!to\b)|"
            r"\b(?:create|add|new)\s+(?:a\s+)?(?:plan|project)\b",
            proposal.source.text,
            re.I,
        ):
            decisions.append(decision.outcome("NO_ACTION", "missing_project_intent"))
            continue
        if proposal.operation == "CREATE_PLAN" and re.search(
            r"\b(?:want|need|needs|have)\s+to\s+(?:start|launch|begin)\b",
            proposal.source.text,
            re.I,
        ):
            decisions.append(decision.outcome("NO_ACTION", "missing_project_intent"))
            continue
        if proposal.operation == "ADD_PLAN_CONTEXT" and re.search(
            r"\b(?:needs?|must|want|remind)\b|\bwill\s+(?:finish|prepare|complete|deliver)\b",
            proposal.source.text,
            re.I,
        ):
            decisions.append(decision.outcome("DENY", "action_is_not_context"))
            continue
        if proposal.classification == "INFORMATION" and proposal.operation != "ADD_PLAN_CONTEXT":
            decisions.append(decision.outcome("NO_ACTION", "information_only"))
            continue
        if proposal.operation == "CREATE_TASK" and re.search(
            r"\b(?:uses?|will use|built with)\b", proposal.source.text, re.I
        ):
            decisions.append(decision.outcome("NO_ACTION", "technology_context"))
            continue
        if proposal.operation != "ADD_PLAN_CONTEXT" and not _ACTION.search(proposal.source.text):
            decisions.append(decision.outcome("NO_ACTION", "missing_commitment"))
            continue
        commitment = re.search(
            r"\b(?:need|needs|must|will|want to|have to|required|create|add)\b",
            proposal.source.text,
            re.I,
        ) or (
            proposal.temporal is not None
            and re.search(r"\bwant\b", proposal.source.text, re.I)
            and re.search(
                r"\b(?:ready|done|finished|completed|running)\b", proposal.source.text, re.I
            )
        )
        if proposal.operation == "CREATE_TASK" and not commitment:
            decisions.append(decision.outcome("NO_ACTION", "missing_commitment"))
            continue
        if proposal.actor == "context" and proposal.operation != "ADD_PLAN_CONTEXT":
            decisions.append(decision.outcome("DENY", "invalid_actor"))
            continue
        if proposal.actor == "user" and re.search(
            r"\b(?:we|our team)\b", proposal.source.text, re.I
        ):
            decisions.append(decision.outcome("CLARIFY", "actor_ambiguity"))
            continue
        if proposal.actor == "team" and not re.search(
            r"\b(?:we|our|team|must|needs?)\b", proposal.source.text, re.I
        ):
            decisions.append(decision.outcome("CLARIFY", "actor_ambiguity"))
            continue
        # Reject a named third party falsely labeled as the user.
        subject = re.match(
            r"\s*([^\W\d_][\w'-]*(?:\s+and\s+[^\W\d_][\w'-]*)?)"
            r"\s+(?:will|needs?|must|has|have)\b",
            proposal.source.text,
            re.I,
        )
        if subject and not re.search(r"\b(?:I|We|Our|The)\b", subject[1], re.I):
            decisions.append(decision.outcome("CLARIFY", "actor_ambiguity"))
            continue
        if proposal.operation == "COMPLETE_TASK" and any(
            int(year) < snapshot.now_utc.year
            for year in re.findall(r"\b((?:19|20)\d{2})\b", proposal.source.text)
        ):
            decisions.append(decision.outcome("NO_ACTION", "historical_evidence"))
            continue
        created = [
            i
            for i, d in enumerate(decisions)
            if d.proposal.operation == "CREATE_PLAN" and d.disposition == "AUTO"
        ]
        if proposal.plan_mention:
            if _plan_name(proposal.plan_mention) not in _plan_name(proposal.source.text):
                decisions.append(decision.outcome("DENY", "ungrounded_plan"))
                continue
            if proposal.operation == "CREATE_PLAN":
                if _plan_name(proposal.plan_mention) != _plan_name(proposal.title):
                    decisions.append(decision.outcome("DENY", "ungrounded_plan"))
                    continue
            else:
                new_matches = [
                    i
                    for i in created
                    if _plan_name(decisions[i].proposal.title) == _plan_name(proposal.plan_mention)
                ]
                if len(new_matches) == 1:
                    decision = replace(decision, plan_id=None, plan_ordinal=new_matches[0])
                else:
                    matches = [
                        p
                        for p in snapshot.plans
                        if _plan_name(p.title) == _plan_name(proposal.plan_mention)
                    ]
                    if new_matches or len(matches) != 1:
                        decisions.append(decision.outcome("CLARIFY", "ambiguous_plan"))
                        continue
                    decision = replace(decision, plan_id=matches[0].id)
                    if decision.plan_id != snapshot.active_plan_id:
                        decisions.append(decision.outcome("CLARIFY", "unloaded_plan_context"))
                        continue
        elif proposal.operation != "CREATE_PLAN" and created:
            if len(created) != 1:
                decisions.append(decision.outcome("CLARIFY", "ambiguous_plan"))
                continue
            decision = replace(decision, plan_id=None, plan_ordinal=created[0])
        if (
            not proposal.plan_mention
            and proposal.operation != "CREATE_PLAN"
            and any(
                d.proposal.operation == "CREATE_PLAN"
                and d.disposition != "AUTO"
                and not (
                    d.reason == "duplicate"
                    and any(
                        p.id == snapshot.active_plan_id
                        and _plan_name(p.title) == _plan_name(d.proposal.title)
                        for p in snapshot.plans
                    )
                )
                for d in decisions
            )
        ):
            # An unresolved new project cannot silently fall back to the old plan.
            decisions.append(decision.outcome("CLARIFY", "ambiguous_plan"))
            continue
        if proposal.operation == "CREATE_PLAN":
            matches = [
                p for p in snapshot.plans if _plan_name(p.title) == _plan_name(proposal.title)
            ]
            if matches:
                disposition = "NO_ACTION" if len(matches) == 1 else "CLARIFY"
                decisions.append(
                    decision.outcome(
                        disposition, "duplicate" if len(matches) == 1 else "ambiguous_plan"
                    )
                )
                continue
        if (
            decision.plan_id is None
            and decision.plan_ordinal is None
            and proposal.operation == "ADD_PLAN_CONTEXT"
        ):
            decisions.append(decision.outcome("CLARIFY", "missing_plan"))
            continue
        at, zone, reason, clock_present = (
            resolve_time(proposal, snapshot, include_clock=True)
            if proposal.operation not in {"COMPLETE_TASK", "ADD_PLAN_CONTEXT"}
            else (None, snapshot.timezone, "", False)
        )
        decision = replace(decision, scheduled_at=at, timezone=zone, has_clock=clock_present)
        if reason:
            decisions.append(decision.outcome("CLARIFY", reason))
            continue
        if proposal.target_mention and not _words(proposal.target_mention) <= _words(
            proposal.source.text
        ):
            decisions.append(decision.outcome("DENY", "ungrounded_target"))
            continue
        kind = (
            "reminder"
            if "REMINDER" in proposal.operation
            else "plan"
            if "PLAN" in proposal.operation
            else "task"
        )
        targets = snapshot.plans if kind == "plan" else snapshot.targets
        title = proposal.target_mention or proposal.title
        update = (
            proposal.operation.startswith("UPDATE_")
            or proposal.operation == "COMPLETE_TASK"
            or proposal.operation in CONFIRMATION_OPERATIONS
        )
        is_pronoun = (
            _is_pronoun_reference(title)
            or (
                proposal.target_mention is not None
                and _is_pronoun_reference(proposal.target_mention)
            )
            or (
                update
                and bool(
                    re.search(
                        r"\b(?:move|reschedule|postpone|mark|complete|finish|cancel|delete|archive)\s+(?:that|it|this)\b",
                        proposal.source.text,
                        re.I,
                    )
                )
            )
        )
        scoped_candidates = [
            t
            for t in targets
            if t.kind == kind
            and (kind == "plan" or decision.plan_ordinal is None and t.plan_id == decision.plan_id)
        ]
        if is_pronoun:
            matches = []
            if snapshot.recent_receipt and snapshot.recent_receipt.saved_actions:
                recent_ids = {
                    str(a.get("id")) for a in snapshot.recent_receipt.saved_actions if a.get("id")
                }
                recent_matches = [t for t in scoped_candidates if str(t.id) in recent_ids]
                if recent_matches:
                    matches = recent_matches
            if not matches:
                matches = scoped_candidates
        else:
            matches = [
                t
                for t in targets
                if t.kind == kind
                and (
                    _plan_name(t.title) == _plan_name(title)
                    if kind == "plan"
                    else _task_title_matches(t.title, title, proposal.source.text)
                    if kind == "task"
                    else _reminder_title_matches(t.title, title, proposal.source.text)
                )
                and (
                    kind == "plan"
                    or decision.plan_ordinal is None
                    and t.plan_id == decision.plan_id
                )
            ]
        if update:
            if len(matches) != 1 or not snapshot.complete:
                decisions.append(
                    decision.outcome(
                        "CLARIFY", "ambiguous_target" if len(matches) > 1 else "missing_target"
                    )
                )
                continue
            decision = replace(
                decision, target_id=matches[0].id, target_revision=matches[0].revision
            )
        elif proposal.operation in {"CREATE_TASK", "CREATE_REMINDER"} and matches:
            target = matches[0]
            if (
                len(matches) == 1
                and target.scheduled_at == at
                and _recurrence_identity(target.recurrence_rule)
                == _recurrence_identity(proposal.recurrence)
            ):
                decisions.append(
                    replace(
                        decision.outcome("NO_ACTION", "duplicate"),
                        target_id=target.id,
                        target_revision=target.revision,
                    )
                )
            else:
                decisions.append(decision.outcome("CLARIFY", "duplicate_schedule_conflict"))
            continue
        normalized_title = (
            " ".join(sorted(_words(proposal.title)))
            if _words(proposal.title)
            else identity(proposal.title)
        )
        key = (
            proposal.operation,
            _plan_name(proposal.title) if proposal.operation == "CREATE_PLAN" else normalized_title,
            decision.plan_id,
            decision.plan_ordinal,
            at,
            _recurrence_identity(proposal.recurrence),
        )
        if key in seen:
            decisions.append(decision.outcome("NO_ACTION", "duplicate"))
            continue
        seen.add(key)
        if not snapshot.complete:
            decisions.append(decision.outcome("CLARIFY", "context_bound"))
        elif proposal.operation in CONFIRMATION_OPERATIONS:
            decisions.append(decision.outcome("CONFIRM", "confirmation_required"))
        else:
            decisions.append(decision)
    # Conflicting updates to the same identity must not appear independently actionable.
    original = tuple(decisions)
    for index, decision in enumerate(original):
        if decision.proposal.operation in {"CREATE_TASK", "CREATE_REMINDER"} and any(
            d.proposal.operation == decision.proposal.operation
            and identity(d.proposal.title) == identity(decision.proposal.title)
            and d.plan_id == decision.plan_id
            and d.plan_ordinal == decision.plan_ordinal
            and d.disposition == "AUTO"
            and decision.disposition == "AUTO"
            and (
                d.scheduled_at != decision.scheduled_at
                or _recurrence_identity(d.proposal.recurrence)
                != _recurrence_identity(decision.proposal.recurrence)
            )
            for i, d in enumerate(original)
            if i != index
        ):
            decisions[index] = decision.outcome("CLARIFY", "duplicate_schedule_conflict")
        if decision.target_id and any(
            d.target_id == decision.target_id
            and d.disposition in {"AUTO", "CONFIRM"}
            and (
                d.proposal.operation != decision.proposal.operation
                or d.scheduled_at != decision.scheduled_at
                or _recurrence_identity(d.proposal.recurrence)
                != _recurrence_identity(decision.proposal.recurrence)
            )
            for i, d in enumerate(original)
            if i != index
        ):
            decisions[index] = replace(
                decision, disposition="CLARIFY", reason="contradictory_actions"
            )
    # A provider cannot bypass the operation bound by folding an explicit list
    # into a single title. Count only direct, validated personal/team obligations.
    for decision in decisions:
        if decision.disposition != "AUTO" or decision.proposal.operation != "CREATE_TASK":
            continue
        enumeration = re.match(
            r"\s*(?:I|we)\s+needs?\s+to\s+(?:do|finish|prepare|complete|write|deliver)\s+(.+)",
            decision.proposal.source.text,
            re.I,
        )
        if (
            enumeration
            and "," in enumeration[1]
            and len(re.split(r",\s*|\s+and\s+", enumeration[1])) > max_actions
        ):
            return tuple(d.outcome("CLARIFY", "action_overflow") for d in decisions)
    return tuple(decisions)
