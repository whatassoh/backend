import hmac


def verify_telegram_secret(
    *,
    received_secret: str | None,
    expected_secret: str,
) -> bool:
    if not received_secret or not expected_secret:
        return False
    return hmac.compare_digest(received_secret, expected_secret)
