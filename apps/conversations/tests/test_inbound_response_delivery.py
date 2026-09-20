from types import SimpleNamespace

import pytest

from apps.conversations.application.orchestrator import ResponseAction, ResponsePlan
from apps.messaging.domain.exceptions import MessagingTemporaryError
from apps.telegram.application.services import process_telegram_webhook
from apps.whatsapp.application.services import process_whatsapp_webhook


def telegram_update(*, update_id=1, message_id=1, text="Bonjour"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1700000000,
            "from": {"id": 123, "is_bot": False, "first_name": "Jacob"},
            "chat": {"id": 456, "type": "private"},
            "text": text,
        },
    }


def whatsapp_update(*, message_id="wamid.1", text="Bonjour"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "phone-id"},
                            "messages": [
                                {
                                    "from": "237699887766",
                                    "id": message_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


def outcome(text="Réponse", actions=()):
    return SimpleNamespace(response=ResponsePlan(text=text, actions=actions))


@pytest.mark.django_db
def test_normal_telegram_text_delivers_outcome_response(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "apps.telegram.application.services.process_inbound_conversation",
        lambda *_args, **_kwargs: outcome("Réponse Telegram"),
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )

    process_telegram_webhook(telegram_update())

    assert [item["response"].text for item in sent] == ["Réponse Telegram"]


@pytest.mark.django_db
def test_normal_whatsapp_text_delivers_outcome_response(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "apps.whatsapp.application.services.process_inbound_conversation",
        lambda *_args, **_kwargs: outcome("Réponse WhatsApp"),
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )

    process_whatsapp_webhook(whatsapp_update())

    assert [item["response"].text for item in sent] == ["Réponse WhatsApp"]


@pytest.mark.django_db
def test_greeting_response_is_delivered(monkeypatch):
    sent = []
    greeting = outcome("Bonjour ! Que souhaitez-vous faire ?")
    monkeypatch.setattr(
        "apps.telegram.application.services.process_inbound_conversation",
        lambda *_args, **_kwargs: greeting,
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )

    process_telegram_webhook(telegram_update(text="Bonjour"))

    assert len(sent) == 1
    assert sent[0]["response"] is greeting.response


@pytest.mark.django_db
def test_interactive_flow_is_delivered_once(monkeypatch):
    sent = []
    interactive = outcome(
        "Que souhaitez-vous faire ?",
        actions=(ResponseAction(action_id="journey.select.buy", label="Acheter"),),
    )
    monkeypatch.setattr(
        "apps.whatsapp.application.services.process_inbound_conversation",
        lambda *_args, **_kwargs: interactive,
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )

    process_whatsapp_webhook(whatsapp_update())

    assert len(sent) == 1
    assert sent[0]["response"].actions == interactive.response.actions


@pytest.mark.django_db
def test_structured_action_does_not_duplicate_response(monkeypatch):
    sent = []
    interactive = outcome(
        "Parfait. Que souhaitez-vous commander ?",
        actions=(ResponseAction(action_id="order.confirm", label="Confirmer"),),
    )
    monkeypatch.setattr(
        "apps.telegram.application.services.process_inbound_conversation",
        lambda *_args, **_kwargs: interactive,
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )
    payload = {
        "update_id": 1,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 123, "is_bot": False, "first_name": "Jacob"},
            "message": {"message_id": 1, "chat": {"id": 456, "type": "private"}},
            "data": "journey.select.buy",
        },
    }

    process_telegram_webhook(payload)

    assert len(sent) == 1


@pytest.mark.django_db
def test_empty_outcome_response_does_not_send(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "apps.telegram.application.services.process_inbound_conversation",
        lambda *_args, **_kwargs: outcome(""),
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )

    process_telegram_webhook(telegram_update())

    assert sent == []


@pytest.mark.django_db
def test_provider_error_does_not_replay_business_processing(monkeypatch):
    processed = []
    response = outcome("Réponse")

    def process(*_args, **_kwargs):
        processed.append(True)
        return response

    monkeypatch.setattr(
        "apps.telegram.application.services.process_inbound_conversation", process
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **_kwargs: (_ for _ in ()).throw(MessagingTemporaryError("down")),
    )
    payload = telegram_update()

    first = process_telegram_webhook(payload)
    second = process_telegram_webhook(payload)

    assert first["processed"] == 1
    assert second["duplicates"] == 1
    assert processed == [True]


@pytest.mark.django_db
def test_duplicate_webhook_does_not_send_second_business_response(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "apps.whatsapp.application.services.process_inbound_conversation",
        lambda *_args, **_kwargs: outcome("Réponse"),
    )
    monkeypatch.setattr(
        "apps.conversations.application.responding.send_response_plan",
        lambda **kwargs: sent.append(kwargs),
    )
    payload = whatsapp_update()

    process_whatsapp_webhook(payload)
    process_whatsapp_webhook(payload)

    assert len(sent) == 1
