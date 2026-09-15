from common.exceptions.domain import DomainError


class OrderDraftMutationError(DomainError):
    code = "ORDER_DRAFT_MUTATION_ERROR"


class InvalidOrderTransition(DomainError):
    code = "INVALID_ORDER_TRANSITION"


class OrderNotEditable(DomainError):
    code = "ORDER_NOT_EDITABLE"


class OrderNotConfirmable(DomainError):
    code = "ORDER_NOT_CONFIRMABLE"


class OrderNotRetryable(DomainError):
    code = "ORDER_NOT_RETRYABLE"


class InvalidFailureCode(DomainError):
    code = "INVALID_FAILURE_CODE"


class InvalidCancellation(DomainError):
    code = "INVALID_CANCELLATION"


class InvalidOrderState(DomainError):
    code = "INVALID_ORDER_STATE"
