from apps.evaluation.application.services import DatasetSanitizer, _split_for


def test_sanitizer_removes_pii_and_coordinates_before_persistence_or_export():
    sanitizer = DatasetSanitizer()
    assert "699" not in sanitizer.sanitize_text("Appelez +237 699 00 00 00 ou a@b.com")
    data = sanitizer.sanitize_output({"latitude": 4.0, "longitude": 9.7, "name": "riz"})
    assert data["latitude"] == "[REDACTED]"
    assert data["longitude"] == "[REDACTED]"


def test_split_is_deterministic_for_a_conversation_group():
    assert _split_for("group-1") == _split_for("group-1")
