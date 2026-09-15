import re

from django.core.exceptions import ValidationError

_PHONE_SEPARATORS_RE = re.compile(r"[\s().-]+")
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_whatsapp_phone(phone: str) -> str:
    value = _PHONE_SEPARATORS_RE.sub("", phone.strip())

    if value.startswith("00"):
        value = f"+{value[2:]}"
    elif value.startswith("237"):
        value = f"+{value}"
    elif value.startswith("6"):
        value = f"+237{value}"

    if not _E164_RE.fullmatch(value):
        raise ValidationError("Enter a valid WhatsApp phone number in E.164 format.")

    return value
