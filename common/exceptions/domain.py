class DomainError(Exception):
    code = "DOMAIN_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: dict | None = None,
    ):
        super().__init__(message)

        self.message = message
        self.details = details or {}


class InvalidStateTransitionError(DomainError):
    code = "INVALID_STATE_TRANSITION"


class ConflictError(DomainError):
    code = "CONFLICT"


class ResourceAlreadyAssignedError(DomainError):
    code = "RESOURCE_ALREADY_ASSIGNED"
