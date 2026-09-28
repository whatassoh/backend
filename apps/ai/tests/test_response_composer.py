from types import SimpleNamespace

from apps.ai.application.composer import NaturalResponseComposer
from apps.ai.application.response_evaluation import evaluate_response
from apps.ai.domain.response import ResponseBrief
from apps.conversations.application.orchestrator import ResponseAction, ResponsePlan


class StubAI:
    def __init__(self, text, speech_acts=()):
        self.text = text
        self.speech_acts = list(speech_acts)
        self.request = None

    def generate_structured(self, request):
        self.request = request
        return SimpleNamespace(
            data={"text": self.text, "speech_acts": self.speech_acts}
        )


class FailingAI:
    def generate_structured(self, request):
        raise RuntimeError("provider unavailable")


def brief(text):
    return ResponseBrief(
        objective="answer",
        authoritative_text=text,
        style={"register": "casual", "address_form": "tu"},
        channel="WHATSAPP",
    )


def test_composer_preserves_structured_response_fields():
    response = ResponsePlan(
        text="Votre commande contient 2 articles.",
        actions=(ResponseAction(action_id="order.confirm", label="Confirmer"),),
        request_location=True,
        metadata={"trusted": True},
    )

    composed, accepted = NaturalResponseComposer(
        StubAI("Ta commande contient 2 articles.")
    ).compose(brief(response.text), response)

    assert accepted is True
    assert composed.text == "Ta commande contient 2 articles."
    assert composed.actions == response.actions
    assert composed.request_location is True
    assert composed.metadata == {"trusted": True}


def test_composer_rejects_a_novel_number_and_returns_baseline():
    response = ResponsePlan(text="Votre commande contient 2 articles.")

    composed, accepted = NaturalResponseComposer(
        StubAI("Ta commande contient 3 articles.")
    ).compose(brief(response.text), response)

    assert accepted is False
    assert composed is response


def test_response_evaluation_enforces_facts_repetition_and_tone():
    evaluation = evaluate_response(
        baseline="Votre commande contient 2 articles.",
        candidate="Ta commande contient 2 articles.",
        previous_text="Je recherche maintenant une boutique.",
        style={"address_form": "tu", "emoji_level": "none", "verbosity": "concise"},
    )

    assert evaluation.factual_shape == 1.0
    assert evaluation.context_preservation == 1.0
    assert evaluation.tone_alignment == 1.0
    assert evaluation.passes() is True


def test_location_policy_requires_question_location_and_speech_act():
    response = ResponsePlan(text="Partagez votre position ?", request_location=True)
    policy_brief = ResponseBrief(
        objective="request location",
        authoritative_text=response.text,
        style={"register": "casual", "address_form": "tu"},
        channel="WHATSAPP",
        use_case="LOCATION_REQUEST",
        required_speech_acts=("REQUEST_LOCATION",),
        forbidden_speech_acts=("CLAIM_LOCATION_RECEIVED",),
        preserve_question=True,
        require_location_reference=True,
    )

    valid, accepted = NaturalResponseComposer(
        StubAI("Tu peux partager ta position ?", ("REQUEST_LOCATION",))
    ).compose(policy_brief, response)
    invalid, rejected = NaturalResponseComposer(
        StubAI("Merci, c'est reçu.", ("CLAIM_LOCATION_RECEIVED",))
    ).compose(policy_brief, response)

    assert accepted is True
    assert valid.text == "Tu peux partager ta position ?"
    assert rejected is False
    assert invalid is response


def test_composer_failure_does_not_replace_the_authoritative_response():
    response = ResponsePlan(text="Réponse métier sûre.")

    try:
        NaturalResponseComposer(FailingAI()).compose(brief(response.text), response)
    except RuntimeError:
        fallback = response

    assert fallback is response


def test_composer_receives_recent_messages_only_as_untrusted_context():
    response = ResponsePlan(text="WhatAssoh est une plateforme locale.")
    policy_brief = ResponseBrief(
        objective="answer",
        authoritative_text=response.text,
        style={"register": "casual", "address_form": "tu"},
        channel="WHATSAPP",
        metadata={
            "recent_messages": [
                {"role": "assistant", "text": "Je suis l'assistant WhatAssoh."}
            ]
        },
    )
    ai = StubAI("WhatAssoh, c'est toute la plateforme locale.")

    _, accepted = NaturalResponseComposer(ai).compose(policy_brief, response)

    assert accepted is True
    assert ai.request.conversation_context["recent_messages"][0]["role"] == (
        "assistant"
    )
