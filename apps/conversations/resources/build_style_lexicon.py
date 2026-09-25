"""Build the reviewed style lexicon shipped with the application.

This script is deterministic and has no runtime role. Surface forms are expanded from
curated conversational concepts so reviewers can change a family once instead of
manually maintaining thousands of duplicated entries.
"""

import hashlib
import json
import re
import unicodedata
from pathlib import Path

VERSION = "2026.09.24-v2"

CASUAL_TERMS = {
    "fr": {
        "address": [
            "ami",
            "amie",
            "bro",
            "boss",
            "chef",
            "collègue",
            "copain",
            "copine",
            "frangin",
            "frérot",
            "gars",
            "grand",
            "la famille",
            "l'ami",
            "man",
            "mon ami",
            "mon frère",
            "mon gars",
            "partenaire",
            "poto",
            "sista",
        ],
        "greeting": [
            "allô",
            "bien ou bien",
            "bon matin",
            "bonjour encore",
            "coucou",
            "hello",
            "hey",
            "hey hey",
            "salut",
            "salut encore",
            "yo",
        ],
        "reaction": [
            "ah bon",
            "ah d'accord",
            "ah ouais",
            "ça alors",
            "ça marche",
            "carrément",
            "cool",
            "d'accord",
            "grave",
            "impeccable",
            "nickel",
            "okay",
            "ok",
            "okko",
            "parfait",
            "sérieux",
            "super",
            "top",
            "trop bien",
            "vas-y",
            "voilà",
            "yes",
        ],
        "filler": [
            "au fait",
            "bon",
            "bon ben",
            "bref",
            "du coup",
            "en fait",
            "genre",
            "honnêtement",
            "juste",
            "mais bon",
            "quand même",
            "sinon",
            "tu vois",
            "vois-tu",
            "voilà quoi",
        ],
        "softener": [
            "s'il te plaît",
            "s il te plait",
            "stp",
            "si possible",
            "quand tu peux",
            "vite fait",
            "deux secondes",
            "un instant",
            "pour moi",
            "pour voir",
        ],
    },
    "en": {
        "address": [
            "buddy",
            "bro",
            "chief",
            "dude",
            "friend",
            "mate",
            "my friend",
            "my guy",
            "pal",
            "partner",
            "sis",
        ],
        "greeting": [
            "hey",
            "hey again",
            "hey there",
            "hello again",
            "hi",
            "hi again",
            "yo",
        ],
        "reaction": [
            "absolutely",
            "alright",
            "awesome",
            "cool",
            "fair enough",
            "for real",
            "got it",
            "great",
            "nice",
            "no way",
            "okay",
            "perfect",
            "right",
            "seriously",
            "sounds good",
            "sure",
            "sweet",
            "yep",
        ],
        "filler": [
            "actually",
            "anyway",
            "basically",
            "by the way",
            "honestly",
            "I mean",
            "just",
            "like",
            "look",
            "so",
            "still",
            "well",
            "you know",
        ],
        "softener": [
            "please",
            "when you can",
            "if you can",
            "real quick",
            "for me",
            "just a sec",
            "a second please",
        ],
    },
}

FR_ACTIONS = [
    "aider",
    "annuler ça",
    "chercher ça",
    "confirmer ça",
    "continuer",
    "corriger ça",
    "détailler",
    "dire où on en est",
    "effacer ça",
    "expliquer",
    "faire ça",
    "garder ça",
    "lancer ça",
    "me dire",
    "me guider",
    "me montrer",
    "mettre à jour",
    "modifier ça",
    "ouvrir ça",
    "préciser",
    "regarder ça",
    "reprendre",
    "résumer",
    "retirer ça",
    "revenir dessus",
    "simplifier",
    "stopper ça",
    "trouver ça",
    "vérifier",
    "vérifier ça",
    "voir ça",
]
EN_ACTIONS = [
    "check it",
    "confirm it",
    "continue",
    "delete it",
    "explain it",
    "find it",
    "fix it",
    "help me",
    "keep it",
    "look at it",
    "open it",
    "remove it",
    "resume",
    "review it",
    "show me",
    "simplify it",
    "stop it",
    "summarize it",
    "tell me",
    "update it",
]

FR_REQUEST_FRAMES = [
    "tu peux {action}",
    "peux-tu {action}",
    "est-ce que tu peux {action}",
    "j'aimerais que tu puisses {action}",
    "je veux juste {action}",
    "on peut {action}",
    "essaie de {action}",
    "vas-y pour {action}",
]
EN_REQUEST_FRAMES = [
    "can you {action}",
    "could you {action}",
    "would you {action}",
    "I just want you to {action}",
    "let's {action}",
    "please {action}",
    "try to {action}",
    "go ahead and {action}",
]

FR_REACTION_ADJECTIVES = [
    "bien",
    "bon",
    "clair",
    "cool",
    "fort",
    "gentil",
    "intéressant",
    "logique",
    "mieux",
    "nickel",
    "normal",
    "pratique",
    "propre",
    "rapide",
    "simple",
    "sympa",
    "top",
    "utile",
]
EN_REACTION_ADJECTIVES = [
    "awesome",
    "clear",
    "cool",
    "easy",
    "fast",
    "good",
    "great",
    "helpful",
    "interesting",
    "logical",
    "nice",
    "perfect",
    "simple",
    "useful",
]

TECHNICAL_TERMS = [
    "api",
    "architecture",
    "async",
    "authentification",
    "authorization",
    "backend",
    "cache",
    "celery",
    "ci/cd",
    "cicd",
    "commit",
    "concurrence",
    "container",
    "database",
    "déploiement",
    "django",
    "docker",
    "endpoint",
    "event",
    "frontend",
    "git",
    "github actions",
    "idempotence",
    "index",
    "infrastructure",
    "json",
    "kubernetes",
    "langgraph",
    "latence",
    "migration",
    "nginx",
    "observabilité",
    "openapi",
    "pipeline",
    "postgresql",
    "prompt",
    "queue",
    "rag",
    "redis",
    "rollback",
    "schema",
    "sdk",
    "sémaphore",
    "serializer",
    "ssh",
    "transaction",
    "webhook",
    "worker",
    "yaml",
]

FORMAL_MARKERS = [
    "auriez-vous l'amabilité",
    "bien cordialement",
    "dans l'attente de votre retour",
    "je vous prie",
    "je vous remercie",
    "pourriez-vous",
    "s'il vous plaît",
    "serait-il possible",
    "veuillez",
    "veuillez agréer",
    "would you kindly",
    "could you please",
    "thank you in advance",
    "kind regards",
]


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", value.casefold()).split())


def add(markers, seen, text, *, locale, category, weight):
    text = normalize(text)
    if not text or text in seen:
        return
    seen.add(text)
    markers.append(
        {"text": text, "locale": locale, "category": category, "weight": weight}
    )


def build_markers():
    markers, seen = [], set()
    for locale, groups in CASUAL_TERMS.items():
        for category, terms in groups.items():
            weight = 2.0 if category in {"address", "filler"} else 1.25
            for term in terms:
                add(
                    markers, seen, term, locale=locale, category=category, weight=weight
                )
        for greeting in groups["greeting"]:
            for address in groups["address"]:
                add(
                    markers,
                    seen,
                    f"{greeting} {address}",
                    locale=locale,
                    category="addressed_greeting",
                    weight=2.5,
                )
        for reaction in groups["reaction"]:
            for address in groups["address"]:
                add(
                    markers,
                    seen,
                    f"{reaction} {address}",
                    locale=locale,
                    category="addressed_reaction",
                    weight=2.25,
                )
        for filler in groups["filler"]:
            for reaction in groups["reaction"]:
                add(
                    markers,
                    seen,
                    f"{filler} {reaction}",
                    locale=locale,
                    category="filler_reaction",
                    weight=1.75,
                )
        for softener in groups["softener"]:
            actions = FR_ACTIONS if locale == "fr" else EN_ACTIONS
            for action in actions:
                add(
                    markers,
                    seen,
                    f"{softener} {action}",
                    locale=locale,
                    category="softened_request",
                    weight=1.5,
                )
    for frame in FR_REQUEST_FRAMES:
        for action in FR_ACTIONS:
            add(
                markers,
                seen,
                frame.format(action=action),
                locale="fr",
                category="direct_request",
                weight=0.75,
            )
    for frame in EN_REQUEST_FRAMES:
        for action in EN_ACTIONS:
            add(
                markers,
                seen,
                frame.format(action=action),
                locale="en",
                category="direct_request",
                weight=0.75,
            )
    for prefix in ("vraiment", "franchement", "super", "trop", "plutot", "assez"):
        for adjective in FR_REACTION_ADJECTIVES:
            add(
                markers,
                seen,
                f"{prefix} {adjective}",
                locale="fr",
                category="expressive_reaction",
                weight=1.0,
            )
    for prefix in ("really", "pretty", "super", "so", "very", "quite"):
        for adjective in EN_REACTION_ADJECTIVES:
            add(
                markers,
                seen,
                f"{prefix} {adjective}",
                locale="en",
                category="expressive_reaction",
                weight=1.0,
            )
    return sorted(
        markers, key=lambda item: (item["locale"], item["category"], item["text"])
    )


def main():
    markers = build_markers()
    if len(markers) < 1000:
        raise RuntimeError(f"The lexicon is unexpectedly small: {len(markers)}")
    payload = {
        "version": VERSION,
        "normalization": "nfkd_casefold_alnum_space",
        "marker_count": len(markers),
        "casual_threshold": 3.0,
        "markers": markers,
        "formal_markers": [normalize(value) for value in FORMAL_MARKERS],
        "technical_terms": [normalize(value) for value in TECHNICAL_TERMS],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    payload["content_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    target = Path(__file__).with_name("casual_markers.json")
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(markers)} markers to {target}")


if __name__ == "__main__":
    main()
