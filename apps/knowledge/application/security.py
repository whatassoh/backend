"""Deterministic controls for untrusted documents and RAG boundaries."""

import base64
import binascii
import re
import unicodedata
from dataclasses import asdict, dataclass

from apps.knowledge.domain.enums import KnowledgeAudience

SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*", re.I),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
}
INJECTION_PATTERNS = {
    "instruction_override": re.compile(
        r"\b(?:ignore|forget|disregard|override|oublie|ignorez?)\b.{0,50}"
        r"\b(?:instructions?|prompt|policy|rules?|consignes?|r[eè]gles?)\b",
        re.I | re.S,
    ),
    "prompt_extraction": re.compile(
        r"\b(?:reveal|print|show|repeat|expose|r[eé]v[eè]le|affiche|montre)\b"
        r".{0,50}\b(?:system prompt|prompt syst[eè]me|hidden instructions?|"
        r"instructions? cach[eé]es?|developer message)\b",
        re.I | re.S,
    ),
    "tool_coercion": re.compile(
        r"\b(?:call|invoke|execute|run)\b.{0,40}\b(?:tool|function|shell|command)\b",
        re.I | re.S,
    ),
    "exfiltration": re.compile(
        r"\b(?:send|upload|exfiltrate)\b.{0,60}\b(?:secret|token|conversation|data)\b",
        re.I | re.S,
    ),
    "role_delimiter": re.compile(
        r"(?:^|\n)\s*(?:system|assistant|developer)\s*:\s*", re.I
    ),
}
HIDDEN_MARKUP_RE = re.compile(
    r"<!--|<(?:script|iframe|object|embed|style)\b|display\s*:\s*none|"
    r"font-size\s*:\s*0|!\[[^]]*]\([^)]*(?:token|secret|data)=",
    re.I,
)
PII_PATTERNS = {
    "email": re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)"),
    "coordinates": re.compile(
        r"\b(?:lat(?:itude)?|lon(?:gitude)?)\s*[:=]\s*-?\d+(?:\.\d+)?", re.I
    ),
}
DYNAMIC_QUERY_PATTERNS = (
    re.compile(
        r"\b(?:prix|tarif|co[uû]te|vaut|stock|en stock|rupture|disponib)\b", re.I
    ),
    re.compile(
        r"\b(?:ma commande|mon colis|ma livraison|suivi de commande|"
        r"position du livreur|o[uù] est (?:ma|mon|le) )\b",
        re.I,
    ),
    re.compile(r"\b(?:price|cost|stock|available|my order|tracking|location)\b", re.I),
)
STATIC_POLICY_CUES = re.compile(
    r"\b(?:politique|r[eè]gle|fonctionne|calcul[eé]|conditions?|guide|aide|"
    r"policy|rules?|how (?:does|do))\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class DocumentSecurityReport:
    accepted: bool
    quarantined: bool
    risk_score: float
    secret_categories: tuple[str, ...]
    injection_categories: tuple[str, ...]
    pii_categories: tuple[str, ...]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        value = asdict(self)
        return {
            key: list(item) if isinstance(item, tuple) else item
            for key, item in value.items()
        }


def scan_document(
    content: str,
    *,
    audience: str,
    trust_level: str,
    public_contact_data: bool = False,
    instructional_security_content: bool = False,
) -> DocumentSecurityReport:
    decoded = _decoded_base64_segments(content)
    inspected_content = "\n".join((content, *decoded))
    secrets = tuple(
        name
        for name, pattern in SECRET_PATTERNS.items()
        if pattern.search(inspected_content)
    )
    injections = tuple(
        name
        for name, pattern in INJECTION_PATTERNS.items()
        if pattern.search(inspected_content)
    )
    if _contains_scrambled_injection(inspected_content):
        injections = tuple(dict.fromkeys((*injections, "obfuscated_instruction")))
    if decoded and injections:
        injections = tuple(dict.fromkeys((*injections, "encoded_instruction")))
    pii = tuple(
        name for name, pattern in PII_PATTERNS.items() if pattern.search(content)
    )
    hidden_markup = bool(HIDDEN_MARKUP_RE.search(content))
    reasons: list[str] = []
    if secrets:
        reasons.append("SECRET_DETECTED")
    if hidden_markup:
        reasons.append("HIDDEN_OR_EXECUTABLE_MARKUP")
    if pii and audience == KnowledgeAudience.PUBLIC and not public_contact_data:
        reasons.append("UNREVIEWED_PUBLIC_PERSONAL_DATA")

    injection_weight = min(0.75, len(injections) * 0.2)
    risk = min(
        1.0,
        (0.9 if secrets else 0)
        + (0.35 if hidden_markup else 0)
        + injection_weight
        + (0.15 if pii else 0),
    )
    unsafe_injection = bool(injections) and not instructional_security_content
    if unsafe_injection:
        reasons.append("UNTRUSTED_INSTRUCTIONAL_CONTENT")
    rejected = bool(secrets)
    quarantined = not rejected and bool(reasons)
    return DocumentSecurityReport(
        accepted=not rejected and not quarantined,
        quarantined=quarantined,
        risk_score=risk,
        secret_categories=secrets,
        injection_categories=injections,
        pii_categories=pii,
        reasons=tuple(reasons),
    )


def requires_authoritative_business_tool(query: str) -> bool:
    """Keep live business truth out of the static knowledge channel."""

    if STATIC_POLICY_CUES.search(query or ""):
        return False
    return any(pattern.search(query or "") for pattern in DYNAMIC_QUERY_PATTERNS)


def safe_evidence_text(value: str, *, maximum: int) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = "".join(
        character for character in value if unicodedata.category(character) != "Cf"
    )
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:maximum]


def _decoded_base64_segments(content: str) -> tuple[str, ...]:
    decoded = []
    for candidate in re.findall(
        r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,500}={0,2}", content
    ):
        try:
            value = base64.b64decode(candidate, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            continue
        if (
            sum(character.isprintable() for character in value) / max(len(value), 1)
            > 0.9
        ):
            decoded.append(value)
    return tuple(decoded[:20])


def _contains_scrambled_injection(content: str) -> bool:
    words = set(re.findall(r"[a-zà-ÿ]{4,20}", content.casefold())[:10_000])

    def equivalent(value: str, target: str) -> bool:
        return (
            len(value) == len(target)
            and value[0] == target[0]
            and value[-1] == target[-1]
            and sorted(value[1:-1]) == sorted(target[1:-1])
        )

    groups = (
        ("ignore", "instructions"),
        ("reveal", "prompt"),
        ("affiche", "système"),
    )
    return any(
        any(equivalent(word, left) for word in words)
        and any(equivalent(word, right) for word in words)
        for left, right in groups
    )
