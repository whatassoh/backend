from apps.conversations.application.preference_engine import (
    extract_explicit_preferences,
    load_preference_lexicon,
)
from apps.conversations.application.style_engine import (
    analyze_writing_style,
    load_style_lexicon,
)


def test_style_lexicon_is_large_unique_and_integrity_checked():
    lexicon = load_style_lexicon()
    marker_count = sum(len(values) for values in lexicon.markers_by_size.values())

    assert lexicon.marker_count == marker_count
    assert lexicon.marker_count >= 1000
    assert lexicon.maximum_ngram >= 3


def test_style_engine_distinguishes_casual_formal_and_technical_signals():
    casual = analyze_writing_style("Salut bro, tu peux vérifier ça stp ?")
    formal = analyze_writing_style(
        "Pourriez-vous vérifier cette commande, s'il vous plaît ?"
    )
    technical = analyze_writing_style("Django Redis Celery webhook API")

    assert casual.casual_score >= 3
    assert casual.language == "fr"
    assert casual.tu_score > casual.vous_score
    assert formal.formal_score >= 3
    assert formal.vous_score > formal.tu_score
    assert technical.technical_score >= 4


def test_preference_lexicon_is_multilingual_and_rejects_conflicts():
    lexicon = load_preference_lexicon()
    entry_count = sum(
        len(group)
        for values in lexicon.entries_by_size.values()
        for group in values.values()
    )
    assert entry_count >= 1000

    french = extract_explicit_preferences(
        "Tutoie-moi, réponds-moi brièvement et sans emoji"
    )
    english = extract_explicit_preferences(
        "From now on, answer in French and keep it short please"
    )
    conflict = extract_explicit_preferences(
        "Réponds brièvement mais donne-moi une réponse complète"
    )

    assert {(item.fact_type, item.value) for item in french} == {
        ("address_form", "tu"),
        ("response_length", "short"),
        ("emoji_preference", "none"),
    }
    assert {(item.fact_type, item.value) for item in english} == {
        ("preferred_language", "fr"),
        ("response_length", "short"),
    }
    assert not any(item.fact_type == "response_length" for item in conflict)


def test_unmapped_message_is_neutral_and_never_rejected():
    text = "zxqv objet totalement absent du lexique 947"

    report = analyze_writing_style(text)
    preferences = extract_explicit_preferences(text)

    assert report.casual_score == 0
    assert report.formal_score == 0
    assert report.evidence_count == 0
    assert preferences == ()
