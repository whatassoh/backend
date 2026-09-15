from common.exceptions.domain import DomainError, InvalidStateTransitionError


class UnknownChannelIdentityError(DomainError):
    code = "UNKNOWN_CHANNEL_IDENTITY"


class ConversationChannelMismatchError(DomainError):
    code = "CONVERSATION_CHANNEL_MISMATCH"


class ConversationAlreadyClosedError(InvalidStateTransitionError):
    code = "CONVERSATION_ALREADY_CLOSED"
