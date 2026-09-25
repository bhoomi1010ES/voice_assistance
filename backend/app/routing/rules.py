"""Narrow deterministic voice routing rules; never constructs write arguments."""

from __future__ import annotations

import re

from app.routing.models import ActionDomain, DecisionSource, RouteDecision, RouteName
from app.services.device_time import resolve_explicit_timezone

MAX_TRANSCRIPT_CHARACTERS = 512

_SCHEDULE_LOOKUP = re.compile(
    r"\b(?:when|what\s+(?:time|date|tasks?|reminders?)|current\s+date|"
    r"which\s+day|do\s+i\s+have|tell\s+me|show|list)\b|"
    r"\bnext\s+(?:task|meeting|appointment|call|event|reminder|medicine|medication)\b",
    re.I,
)
_SCHEDULED_REMINDER = re.compile(r"\b(?:reminders?|medicine|medication|pills?)\b", re.I)
_SCHEDULED_TASK = re.compile(
    r"\b(?:tasks?|meeting|appointment|call|event|deadline|todo|to\s+do)\b", re.I
)
_CURRENT_TIME = re.compile(
    r"^(?:what time is it(?: now)?|what(?: is|'s) the time|"
    r"what(?: is|'s) (?:the )?current time|what is time now|"
    r"tell me (?:the )?time|current time|time now)[?.!]*$",
    re.I,
)
_CURRENT_DATE = re.compile(
    r"^(?:what(?: is|'s) (?:the )?(?:current date|date today|date)|"
    r"what(?: is|'s) today's date|what is today(?:'s)? date|what date is it|"
    r"tell me today's date|current date|today's date|date today)[?.!]*$",
    re.I,
)
_EXPLICIT_ZONE_SUFFIX = re.compile(
    r"\s+(?:(?:in|for)\s+)?(?:new\s+york|nyc|los\s+angeles|la|london|uk|"
    r"kolkata|calcutta|india|ist|isd|tokyo|sydney|utc|gmt|"
    r"[a-z]+/[a-z_]+(?:/[a-z_]+)?)(?:\s+(?:standard\s+)?time)?[?.!]*$",
    re.I,
)
_CURRENT_TIME_MENTION = re.compile(
    r"\b(?:what time is it|current time|time now)\b",
    re.I,
)
_CONTROL = re.compile(r"^(?:stop|pause|cancel)\s+listening[.!]*$", re.I)
_AFFIRMATIVE = {"yes", "yeah", "yep", "okay", "ok", "sure", "confirm"}
_NEGATIVE = {"no", "nope", "cancel", "stop", "never mind", "nevermind"}
_TASK_WRITE = re.compile(
    r"\b(?:create|add|make|update|change|delete|remove|complete|finish)\b"
    r".*\btask\b",
    re.I,
)
_REMINDER_TASK_CREATION = re.compile(
    r"\b(?:remind\s+me\s+to|set\s+(?:up\s+)?(?:a\s+)?reminder|"
    r"create\s+(?:a\s+)?reminder)\b",
    re.I,
)
_REMINDER_MANAGEMENT = re.compile(
    r"\b(?:delete|remove|cancel|change|update|reschedule)"
    r"\s+(?:my|the|a|an)?\s*[^.!?]{0,100}\breminder\b",
    re.I,
)
_MEMORY_QUERY = re.compile(
    r"\b(?:which\s+.{1,50}\s+do\s+i\s+prefer|which\s+.{1,50}\s+did\s+i\s+tell\s+you|"
    r"what\s+.{1,50}\s+do\s+i\s+prefer|what\s+(?:is|was)\s+my\s+"
    r"(?:(?:name|timezone|home|job|workplace)\b|(?:favorite|favourite|preferred)\s+\w+)|"
    r"what(?:\s+is|'s)\s+my\s+.{1,40}\b(?:name|favorite|favourite|preferred|timezone|workplace)\b|"
    r"where\s+(?:do|did)\s+i\s+(?:work|live|usually\s+stay)|"
    r"do\s+i\s+(?:like|prefer|own|use)\b|who\s+is\s+my\s+"
    r"(?:friend|colleague|manager|partner|doctor|neighbor)\b|"
    r"what\s+did\s+i\s+(?:tell|say)\s+you?\s+about|"
    r"what\s+\w+\s+did\s+i\s+say\s+(?:i\s+)?like|"
    r"what\s+(?:time\s+)?did\s+i\s+say\s+i\s+|what\s+have\s+i\s+told\s+you|"
    r"what\s+(?:happened|did\s+i\s+do)\s+(?:today|yesterday|last\s+week)|"
    r"what\s+do\s+you\s+remember\s+about\s+(?:me|my\s+.{1,40}))\b",
    re.I,
)
_MEMORY_SAVE = re.compile(
    r"^(?:remember\s+(?:that\s+)?\S.+|save\s+(?:(?:that|this)\s+)?\S.+)$",
    re.I,
)
_MEMORY_FORGET = re.compile(
    r"^(?:forget\s+(?:that\s+)?\S.+|delete\s+(?:my\s+)?saved\s+\S.+)$",
    re.I,
)
_INFORMATIONAL = re.compile(
    r"^(?:how\s+do\s+i|how\s+can\s+i|how\s+does|what\s+does|what\s+is|"
    r"why\b|should\s+i\b|do\s+i\s+need\s+to\b|can\s+i\b|"
    r"can\s+you\s+explain|explain|tell\s+me\s+about|tell\s+me\s+(?:a\s+)?(?:joke|story))\b",
    re.I,
)
_UNSUPPORTED_LANGUAGE = re.compile(
    r"[\u0900-\u097f\u0a80-\u0aff]|"
    r"\b(?:aaj|kal|kya|ka|ki|hai|meri|mera|mujhe|yaad|pasand|chhe|mare|mane)\b",
    re.I,
)
_ACTION_MENTION = re.compile(
    r"\b(?:create|add|make|remind|delete|remove|change|update|remember|save|forget)\b",
    re.I,
)
_SECOND_INTENT = re.compile(
    r"\b(?:what|when|where|tell\s+me|explain|joke|time|date|today|current)\b",
    re.I,
)
_AMBIGUOUS_ACTION = re.compile(
    r"^(?:create|add|make|update|change|delete|remove|complete|finish|remind|"
    r"remember|forget|save|schedule)(?:\s+(?:me\s+)?(?:about\s+)?"
    r"(?:it|that|this|one|something|a task|a reminder|to call)?)?[.!?]*$|"
    r"^remind\s+me\s+about\s+.+$",
    re.I,
)
_UNFINISHED_WRITE = re.compile(
    r"^(?:create|add|make|update|change|delete|remove|complete|finish|remind|"
    r"remember|forget|save|schedule)\b.*\b(?:to|a|an|my|at|on)$",
    re.I,
)
_SHORT_REMINDER_ACTION = re.compile(r"^remind me to(?:\s+\w+){1,3}[.!?]*$", re.I)
_MALFORMED_STT = re.compile(r"\[(?:unintelligible|noise|unknown|hallucinated)[^\]]*\]", re.I)
_MEMORY_POLICY_CHANGE = re.compile(
    r"\b(?:don't|do not)\s+(?:use|save|store).{0,80}\bmemory\b",
    re.I,
)
_UNCERTAIN_SCHEDULE_CHANGE = re.compile(
    r"^(?:move|reschedule|change|update)\s+(?:my|the)\s+"
    r"(?:appointment|meeting|event|call)\b",
    re.I,
)


def classify_transcript(transcript: str) -> RouteDecision:
    """Return a validated, non-executable route proposal for a bounded transcript."""

    if not isinstance(transcript, str) or len(transcript) > MAX_TRANSCRIPT_CHARACTERS:
        raise ValueError(
            f"transcript must be text of at most {MAX_TRANSCRIPT_CHARACTERS} characters"
        )
    text = " ".join(transcript.casefold().split()).strip()
    if not text:
        return _decision(RouteName.MIXED_AMBIGUOUS)

    # A pending confirmation is resolved by the authenticated gateway callback
    # before this function is called. Bare yes/no and unclear controls are not
    # interpreted as actions here.
    if text in _AFFIRMATIVE or text in _NEGATIVE:
        return _decision(RouteName.MIXED_AMBIGUOUS)
    if _CONTROL.fullmatch(text):
        return _decision(RouteName.CONTROL)
    if re.match(r"^(?:stop|cancel)\b", text):
        return _decision(RouteName.MIXED_AMBIGUOUS)

    if _UNSUPPORTED_LANGUAGE.search(text):
        return _decision(RouteName.MIXED_AMBIGUOUS)
    if re.search(r"\b([\w']+)\b(?:\s+\1\b){1,}", text, re.I):
        return _decision(RouteName.MIXED_AMBIGUOUS)
    if _UNFINISHED_WRITE.fullmatch(text) or _SHORT_REMINDER_ACTION.fullmatch(text):
        return _decision(RouteName.MIXED_AMBIGUOUS)

    # Definite informational prefixes win over action keywords. Explicit saved
    # item reads are classified below before the clock rules.
    informational = bool(_INFORMATIONAL.search(text))

    actions = len(_ACTION_MENTION.findall(text))
    compound = bool(re.search(r"\b(?:and|then)\b", text)) and (
        actions > 1
        or (actions > 0 and _SECOND_INTENT.search(text))
        or (_CURRENT_TIME_MENTION.search(text) and _SECOND_INTENT.search(text))
    )
    if compound:
        return _decision(RouteName.MIXED_AMBIGUOUS)

    if _MEMORY_QUERY.search(text):
        return _decision(RouteName.MEMORY_QUERY)

    # Topic words such as "tasks" in a joke/story request are not a schedule
    # lookup. Handle this narrow informational form before schedule keywords.
    if re.match(r"^tell\s+me\s+(?:a\s+)?(?:joke|story)\b", text):
        return _decision(RouteName.GENERAL_LLM)

    asks_about_schedule = bool(_SCHEDULE_LOOKUP.search(text))
    if asks_about_schedule and _SCHEDULED_REMINDER.search(text):
        date_window = _requested_date_window(text)
        search_terms = _structured_search_terms(text)
        return _decision(
            RouteName.STRUCTURED_READ,
            target_tool="list_reminders",
            read_arguments={
                "status": _requested_reminder_status(text),
                "upcoming": date_window is None,
                "limit": 20,
                "date_window": date_window,
                "next_only": bool(re.search(r"\bnext\b", text)) or date_window is None,
                "search_terms": search_terms,
            },
        )
    if asks_about_schedule and _SCHEDULED_TASK.search(text):
        date_window = _requested_date_window(text)
        return _decision(
            RouteName.STRUCTURED_READ,
            target_tool="list_tasks",
            read_arguments={
                "status": _requested_task_status(text),
                "limit": 20,
                "date_window": date_window,
                "next_only": bool(re.search(r"\bnext\b", text))
                or (
                    date_window is None
                    and bool(re.search(r"\b(?:when|what time|what date)\b", text))
                ),
                "active_only": _requested_task_status(text) is None,
                "search_terms": _structured_search_terms(text),
            },
        )

    if (
        re.search(r"\b(?:time|clock)\b", text)
        and re.search(r"\b(?:date|today)\b", text)
        and re.search(r"\b(?:current|today|now|what|tell me)\b", text)
    ) or (re.search(r"\bremind me\b", text) and re.search(r"\bwhat time (?:is it|it is)\b", text)):
        return _decision(RouteName.MIXED_AMBIGUOUS)

    if _MALFORMED_STT.search(text):
        return _decision(RouteName.MIXED_AMBIGUOUS)

    if _clock_request_with_explicit_timezone(_CURRENT_TIME, text):
        return _decision(RouteName.DIRECT_TOOL, target_tool="get_current_time")
    if _clock_request_with_explicit_timezone(_CURRENT_DATE, text):
        return _decision(RouteName.DIRECT_TOOL, target_tool="get_current_date")

    if informational:
        return _decision(RouteName.GENERAL_LLM)

    intent_hits = sum(
        bool(pattern.search(text))
        for pattern in (
            _TASK_WRITE,
            _REMINDER_TASK_CREATION,
            _REMINDER_MANAGEMENT,
            _MEMORY_SAVE,
            _MEMORY_FORGET,
        )
    )
    if intent_hits > 1:
        return _decision(RouteName.MIXED_AMBIGUOUS)

    if _TASK_WRITE.search(text):
        return _decision(RouteName.TASK_ACTION, action_domain=ActionDomain.TASK)
    if _REMINDER_TASK_CREATION.search(text):
        # Existing voice behavior creates a task through the confirmation-
        # required `create_task` path for “remind me to …” requests.
        return _decision(RouteName.TASK_ACTION, action_domain=ActionDomain.TASK)
    if _REMINDER_MANAGEMENT.search(text):
        return _decision(RouteName.TASK_ACTION, action_domain=ActionDomain.REMINDER)
    if _MEMORY_SAVE.fullmatch(text):
        return _decision(RouteName.MEMORY_ACTION, action_domain=ActionDomain.MEMORY_SAVE)
    if _MEMORY_FORGET.fullmatch(text):
        return _decision(RouteName.MEMORY_ACTION, action_domain=ActionDomain.MEMORY_FORGET)
    if (
        _MEMORY_POLICY_CHANGE.search(text)
        or _UNCERTAIN_SCHEDULE_CHANGE.search(text)
        or _AMBIGUOUS_ACTION.fullmatch(text)
    ):
        return _decision(RouteName.MIXED_AMBIGUOUS)
    return _decision(RouteName.GENERAL_LLM)


def _clock_request_with_explicit_timezone(pattern: re.Pattern[str], text: str) -> bool:
    if pattern.fullmatch(text):
        return True
    if resolve_explicit_timezone(text) is None:
        return False
    request_without_zone = _EXPLICIT_ZONE_SUFFIX.sub("", text).strip()
    return bool(request_without_zone and pattern.fullmatch(request_without_zone))


def _requested_date_window(text: str) -> str | None:
    if re.search(r"\btoday\b", text):
        return "today"
    if re.search(r"\btomorrow\b", text):
        return "tomorrow"
    return None


def _requested_task_status(text: str) -> str | None:
    if re.search(r"\b(?:completed|complete|finished|done)\b", text):
        return "completed"
    if re.search(r"\b(?:cancelled|canceled)\b", text):
        return "cancelled"
    if re.search(r"\bin progress\b", text):
        return "in_progress"
    if re.search(r"\bpending\b", text):
        return "pending"
    return None


def _requested_reminder_status(text: str) -> str:
    for status in ("sent", "failed", "cancelled"):
        if re.search(rf"\b{status}\b", text):
            return status
    return "scheduled"


def _structured_search_terms(text: str) -> list[str]:
    terms = (
        "medicine",
        "medication",
        "pill",
        "pills",
        "meeting",
        "appointment",
        "call",
        "event",
        "deadline",
    )
    return [term for term in terms if re.search(rf"\b{term}\b", text)][:3]


def _decision(route: RouteName, **fields: object) -> RouteDecision:
    return RouteDecision(
        route=route,
        decision_source=DecisionSource.RULE,
        confidence=1.0,
        **fields,
    )
