"""Generate explicit communication-preference phrases from reviewed templates."""

import hashlib
import json
from pathlib import Path

from apps.conversations.application.style_engine import normalize_style_text

VERSION = "2026.09.24-v2"

BASE_PHRASES = {
    ("address_form", "tu", "fr"): [
        "tutoie moi",
        "tu peux me tutoyer",
        "utilise le tutoiement",
        "parle moi en me tutoyant",
        "adresse toi a moi avec tu",
    ],
    ("address_form", "vous", "fr"): [
        "vouvoie moi",
        "vous pouvez me vouvoyer",
        "utilise le vouvoiement",
        "parlez moi en me vouvoyant",
        "adressez vous a moi avec vous",
    ],
    ("address_form", "tu", "en"): [
        "address me informally",
        "use informal language with me",
        "be informal with me",
    ],
    ("address_form", "vous", "en"): [
        "address me formally",
        "use formal language with me",
        "be formal with me",
    ],
    ("response_length", "short", "fr"): [
        "reponds brievement",
        "reponds moi brievement",
        "fais court",
        "sois bref",
        "donne moi une reponse courte",
        "va droit au but",
        "reste concis",
    ],
    ("response_length", "detailed", "fr"): [
        "reponds en detail",
        "reponds moi en detail",
        "sois detaille",
        "donne moi une reponse complete",
        "explique tout",
        "developpe ta reponse",
    ],
    ("response_length", "short", "en"): [
        "keep it short",
        "be brief",
        "answer briefly",
        "give me a short answer",
        "get to the point",
        "stay concise",
    ],
    ("response_length", "detailed", "en"): [
        "answer in detail",
        "be detailed",
        "give me a complete answer",
        "explain everything",
        "expand your answer",
    ],
    ("emoji_preference", "none", "fr"): [
        "sans emoji",
        "n utilise pas d emoji",
        "ne mets pas d emoji",
        "je ne veux pas d emoji",
        "evite les emoji",
        "zero emoji",
    ],
    ("emoji_preference", "light", "fr"): [
        "avec des emoji",
        "tu peux utiliser des emoji",
        "mets quelques emoji",
        "ajoute un peu d emoji",
        "utilise parfois des emoji",
    ],
    ("emoji_preference", "none", "en"): [
        "without emojis",
        "do not use emojis",
        "no emojis",
        "avoid emojis",
    ],
    ("emoji_preference", "light", "en"): [
        "use emojis",
        "you can use emojis",
        "add a few emojis",
        "use some emojis",
    ],
    ("preferred_language", "fr", "fr"): [
        "parle moi en francais",
        "reponds en francais",
        "continue en francais",
        "utilise le francais",
        "je prefere le francais",
    ],
    ("preferred_language", "en", "fr"): [
        "parle moi en anglais",
        "reponds en anglais",
        "continue en anglais",
        "utilise l anglais",
        "je prefere l anglais",
    ],
    ("preferred_language", "fr", "en"): [
        "speak french",
        "answer in french",
        "continue in french",
        "use french",
    ],
    ("preferred_language", "en", "en"): [
        "speak english",
        "answer in english",
        "continue in english",
        "use english",
    ],
    ("technical_level", "technical", "fr"): [
        "sois technique",
        "donne moi les details techniques",
        "utilise les termes techniques",
        "ne simplifie pas la technique",
        "je veux une explication technique",
    ],
    ("technical_level", "simple", "fr"): [
        "explique simplement",
        "sans jargon",
        "evite les termes techniques",
        "vulgarise ta reponse",
        "je veux une explication simple",
    ],
    ("technical_level", "technical", "en"): [
        "be technical",
        "give me the technical details",
        "use technical terms",
        "do not simplify the technical details",
    ],
    ("technical_level", "simple", "en"): [
        "explain simply",
        "without jargon",
        "avoid technical terms",
        "give me a simple explanation",
    ],
}

PREFIXES = {
    "fr": [
        "",
        "a partir de maintenant",
        "desormais",
        "pour cette conversation",
        "dans tes prochaines reponses",
    ],
    "en": [
        "",
        "from now on",
        "for this conversation",
        "in your next answers",
    ],
}

SUFFIXES = {
    "fr": ["", "s il te plait", "avec moi", "dans tes prochaines reponses"],
    "en": ["", "please", "with me", "in your next answers"],
}


def main():
    entries = []
    seen = set()
    for (fact_type, value, locale), phrases in BASE_PHRASES.items():
        for phrase in phrases:
            for prefix in PREFIXES[locale]:
                for suffix in SUFFIXES[locale]:
                    text = normalize_style_text(" ".join((prefix, phrase, suffix)))
                    key = (fact_type, value, text)
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(
                        {
                            "fact_type": fact_type,
                            "value": value,
                            "locale": locale,
                            "text": text,
                        }
                    )
    entries.sort(key=lambda item: (item["fact_type"], item["value"], item["text"]))
    payload = {"version": VERSION, "entry_count": len(entries), "entries": entries}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    payload["content_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    target = Path(__file__).with_name("communication_preferences.json")
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(entries)} preferences to {target}")


if __name__ == "__main__":
    main()
