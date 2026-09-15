import hashlib
import hmac


def verify_meta_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    if not signature_header or not app_secret:
        return False
    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False

    received = signature_header[len(prefix) :]
    expected = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received)
