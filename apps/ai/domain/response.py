from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseBrief:
    """Facts and presentation constraints; never grants permission to act."""

    objective: str
    authoritative_text: str
    style: dict[str, str]
    channel: str
    active_task: str | None = None
    valid_options: tuple[str, ...] = ()
    required_facts: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    use_case: str = "GENERIC"
    required_speech_acts: tuple[str, ...] = ()
    forbidden_speech_acts: tuple[str, ...] = ()
    max_characters: int = 1200
    preserve_question: bool = False
    preserve_line_structure: bool = False
    require_location_reference: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
