from apps.conversations.infrastructure.models import Conversation
from apps.whatsapp.domain.enums import WhatsAppOutboundDecision
from apps.whatsapp.domain.service_window import WhatsAppServiceWindowPolicy


class WhatsAppOutboundPolicy:
    def __init__(
        self,
        service_window_policy: WhatsAppServiceWindowPolicy | None = None,
    ):
        self.service_window_policy = (
            service_window_policy or WhatsAppServiceWindowPolicy()
        )

    def decide(
        self,
        conversation: Conversation,
        *,
        now=None,
    ) -> WhatsAppOutboundDecision:
        if self.service_window_policy.is_open(conversation, now=now):
            return WhatsAppOutboundDecision.FREE_FORM_ALLOWED
        return WhatsAppOutboundDecision.TEMPLATE_REQUIRED
