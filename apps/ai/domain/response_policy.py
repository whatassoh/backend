"""Typed presentation policies selected after the authoritative business decision."""

from dataclasses import dataclass
from enum import StrEnum


class ResponseUseCase(StrEnum):
    SOCIAL = "SOCIAL"
    CLARIFICATION = "CLARIFICATION"
    LOCATION_REQUEST = "LOCATION_REQUEST"
    ONBOARDING = "ONBOARDING"
    ORDER_REVIEW = "ORDER_REVIEW"
    ORDER_STATUS = "ORDER_STATUS"
    ACTION_RESULT = "ACTION_RESULT"
    NAVIGATION = "NAVIGATION"
    CATALOG = "CATALOG"
    EMPTY_RESULT = "EMPTY_RESULT"
    MEMORY_CONTROL = "MEMORY_CONTROL"
    HELP = "HELP"
    ERROR_RECOVERY = "ERROR_RECOVERY"
    GENERIC = "GENERIC"


@dataclass(frozen=True, slots=True)
class ResponsePolicy:
    use_case: ResponseUseCase
    objective: str
    required_speech_acts: tuple[str, ...] = ()
    forbidden_speech_acts: tuple[str, ...] = ()
    max_characters: int = 1200
    preserve_question: bool = False
    preserve_line_structure: bool = False
    require_location_reference: bool = False
    composable: bool = True


POLICIES = {
    ResponseUseCase.SOCIAL: ResponsePolicy(
        ResponseUseCase.SOCIAL,
        "Respond warmly without repeating an introduction or forcing the pending task.",
        required_speech_acts=("SOCIAL_ACKNOWLEDGEMENT",),
        forbidden_speech_acts=("CLAIM_ACTION_EXECUTED",),
        max_characters=500,
    ),
    ResponseUseCase.CLARIFICATION: ResponsePolicy(
        ResponseUseCase.CLARIFICATION,
        "Ask one precise question and preserve every valid option.",
        required_speech_acts=("ASK_CLARIFICATION",),
        forbidden_speech_acts=("CLAIM_ACTION_EXECUTED", "INVENT_OPTION"),
        max_characters=700,
        preserve_question=True,
    ),
    ResponseUseCase.LOCATION_REQUEST: ResponsePolicy(
        ResponseUseCase.LOCATION_REQUEST,
        "Request a location while keeping pause, back and cancel available.",
        required_speech_acts=("REQUEST_LOCATION",),
        forbidden_speech_acts=("CLAIM_LOCATION_RECEIVED",),
        max_characters=700,
        preserve_question=True,
        require_location_reference=True,
    ),
    ResponseUseCase.ONBOARDING: ResponsePolicy(
        ResponseUseCase.ONBOARDING,
        "Explain the current onboarding step and ask only for its missing input.",
        required_speech_acts=("GUIDE_NEXT_STEP",),
        forbidden_speech_acts=("CLAIM_ONBOARDING_COMPLETED",),
        max_characters=900,
    ),
    ResponseUseCase.ORDER_REVIEW: ResponsePolicy(
        ResponseUseCase.ORDER_REVIEW,
        "Present the exact order review and request explicit confirmation.",
        required_speech_acts=("PRESENT_REVIEW", "REQUEST_CONFIRMATION"),
        forbidden_speech_acts=("CLAIM_ORDER_CONFIRMED",),
        max_characters=1800,
        preserve_question=True,
        preserve_line_structure=True,
    ),
    ResponseUseCase.ORDER_STATUS: ResponsePolicy(
        ResponseUseCase.ORDER_STATUS,
        "State only the authoritative order or delivery status.",
        required_speech_acts=("REPORT_STATUS",),
        forbidden_speech_acts=("INVENT_STATUS", "PROMISE_DEADLINE"),
        max_characters=1200,
        preserve_line_structure=True,
    ),
    ResponseUseCase.ACTION_RESULT: ResponsePolicy(
        ResponseUseCase.ACTION_RESULT,
        "Acknowledge exactly the action that the backend reports as completed.",
        required_speech_acts=("REPORT_ACTION_RESULT",),
        forbidden_speech_acts=("INVENT_ACTION_RESULT",),
        max_characters=900,
    ),
    ResponseUseCase.NAVIGATION: ResponsePolicy(
        ResponseUseCase.NAVIGATION,
        "Explain pause, resume, back or cancellation without losing task state.",
        required_speech_acts=("REPORT_NAVIGATION",),
        forbidden_speech_acts=("INVENT_ACTION_RESULT",),
        max_characters=700,
    ),
    ResponseUseCase.CATALOG: ResponsePolicy(
        ResponseUseCase.CATALOG,
        "Describe the authoritative catalog result and preserve product distinctions.",
        required_speech_acts=("REPORT_CATALOG_RESULT",),
        forbidden_speech_acts=("INVENT_PRICE", "INVENT_STOCK", "INVENT_PRODUCT"),
        max_characters=1600,
        preserve_line_structure=True,
    ),
    ResponseUseCase.EMPTY_RESULT: ResponsePolicy(
        ResponseUseCase.EMPTY_RESULT,
        "Explain what was not found and offer only real next actions.",
        required_speech_acts=("REPORT_EMPTY_RESULT",),
        forbidden_speech_acts=("INVENT_RESULT",),
        max_characters=900,
    ),
    ResponseUseCase.MEMORY_CONTROL: ResponsePolicy(
        ResponseUseCase.MEMORY_CONTROL,
        "Report only communication preferences read or erased for this user.",
        required_speech_acts=("REPORT_MEMORY_CONTROL",),
        forbidden_speech_acts=("REVEAL_OTHER_USER", "INVENT_MEMORY"),
        max_characters=900,
    ),
    ResponseUseCase.HELP: ResponsePolicy(
        ResponseUseCase.HELP,
        "Answer the supported help request and disclose limits clearly.",
        required_speech_acts=("PROVIDE_HELP",),
        forbidden_speech_acts=("INVENT_CAPABILITY",),
        max_characters=1400,
    ),
    ResponseUseCase.ERROR_RECOVERY: ResponsePolicy(
        ResponseUseCase.ERROR_RECOVERY,
        "Explain the recoverable problem without exposing internals or claiming success.",
        required_speech_acts=("REPORT_RECOVERABLE_ERROR",),
        forbidden_speech_acts=("CLAIM_ACTION_EXECUTED", "REVEAL_INTERNAL_ERROR"),
        max_characters=700,
        composable=False,
    ),
    ResponseUseCase.GENERIC: ResponsePolicy(
        ResponseUseCase.GENERIC,
        "Answer the current request using only the authoritative response.",
        forbidden_speech_acts=("INVENT_FACT", "CLAIM_ACTION_EXECUTED"),
        max_characters=1200,
    ),
}
