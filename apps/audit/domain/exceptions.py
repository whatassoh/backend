from common.exceptions.domain import DomainError


class AuditEventImmutableError(DomainError):
    code = "AUDIT_EVENT_IMMUTABLE"
