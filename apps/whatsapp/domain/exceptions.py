from apps.whatsapp.exceptions import WhatsAppError


class WhatsAppTemplateError(WhatsAppError):
    pass


class WhatsAppTemplateRequired(WhatsAppTemplateError):
    pass


class WhatsAppTemplateConfigurationError(WhatsAppTemplateError):
    pass


class WhatsAppTemplateValidationError(WhatsAppTemplateError):
    pass


class WhatsAppInteractiveValidationError(WhatsAppError):
    pass
