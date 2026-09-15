import secrets
import string

from django.utils import timezone

ORDER_REFERENCE_PREFIX = "ORD"
ORDER_REFERENCE_SUFFIX_LENGTH = 4
ORDER_REFERENCE_ALPHABET = "".join(
    char
    for char in string.ascii_uppercase + string.digits
    if char not in {"O", "0", "I", "1"}
)


def generate_order_reference(*, created_at=None) -> str:
    created_at = created_at or timezone.localtime()
    date_part = created_at.strftime("%y%m%d")
    suffix = "".join(
        secrets.choice(ORDER_REFERENCE_ALPHABET)
        for _ in range(ORDER_REFERENCE_SUFFIX_LENGTH)
    )

    return f"{ORDER_REFERENCE_PREFIX}-{date_part}-{suffix}"
