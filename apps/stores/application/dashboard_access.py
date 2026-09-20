"""Opaque magic-link issuance/consumption for the seller dashboard."""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.application.services import record_audit_event
from apps.audit.domain.enums import AuditActorType, AuditEntityType, AuditEventType
from apps.stores.application.permissions import can_manage_store
from apps.stores.domain.enums import StoreMembershipStatus
from apps.stores.infrastructure.models import SellerDashboardAccessGrant, StoreMembership

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class DashboardAccessResult:
    status: str
    url: str | None = None
    user: object | None = None


class SellerDashboardAccessService:
    purpose = "SELLER_DASHBOARD"

    def issue_access_link(self, *, actor, conversation=None, correlation_id=None):
        memberships = StoreMembership.objects.filter(
            user=actor, status=StoreMembershipStatus.ACTIVE
        ).select_related("store")
        if not any(can_manage_store(actor, membership.store) for membership in memberships):
            self._log("seller_dashboard_access_denied", actor, correlation_id)
            return DashboardAccessResult(status="FORBIDDEN")
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash(raw_token)
        now = timezone.now()
        with transaction.atomic():
            SellerDashboardAccessGrant.objects.filter(
                user=actor, purpose=self.purpose, consumed_at__isnull=True,
                revoked_at__isnull=True, expires_at__gt=now,
            ).update(revoked_at=now)
            grant = SellerDashboardAccessGrant.objects.create(
                user=actor, token_hash=token_hash, purpose=self.purpose,
                expires_at=now + timedelta(seconds=settings.SELLER_DASHBOARD_LINK_TTL_SECONDS),
            )
            record_audit_event(
                actor=actor, actor_type=AuditActorType.USER,
                entity_type=AuditEntityType.SELLER_DASHBOARD_ACCESS,
                entity_id=grant.id, event_type=AuditEventType.SELLER_DASHBOARD_ACCESS_ISSUED,
                correlation_id=correlation_id,
            )
        # Fragment is never sent as an HTTP request or Referer; frontend exchanges
        # it immediately by POST then replaces browser history with a clean URL.
        url = settings.SELLER_DASHBOARD_FRONTEND_URL.rstrip("/") + "#" + urlencode({"token": raw_token})
        self._log("seller_dashboard_link_issued", actor, correlation_id)
        return DashboardAccessResult(status="ISSUED", url=url)

    @transaction.atomic
    def consume(self, *, raw_token, correlation_id=None):
        if not isinstance(raw_token, str) or len(raw_token) < 32:
            self._log("seller_dashboard_link_invalid", None, correlation_id)
            return DashboardAccessResult(status="INVALID")
        grant = SellerDashboardAccessGrant.objects.select_for_update().select_related("user").filter(
            token_hash=self._hash(raw_token), purpose=self.purpose
        ).first()
        if grant is None:
            self._log("seller_dashboard_link_invalid", None, correlation_id)
            return DashboardAccessResult(status="INVALID")
        now = timezone.now()
        if grant.consumed_at or grant.revoked_at:
            self._record_rejected(grant, correlation_id)
            self._log("seller_dashboard_link_invalid", grant.user, correlation_id)
            return DashboardAccessResult(status="USED")
        if grant.expires_at <= now:
            self._record_rejected(grant, correlation_id)
            self._log("seller_dashboard_link_expired", grant.user, correlation_id)
            return DashboardAccessResult(status="EXPIRED")
        grant.consumed_at = now
        grant.save(update_fields=["consumed_at", "updated_at"])
        record_audit_event(
            actor=grant.user, actor_type=AuditActorType.USER,
            entity_type=AuditEntityType.SELLER_DASHBOARD_ACCESS,
            entity_id=grant.id, event_type=AuditEventType.SELLER_DASHBOARD_ACCESS_CONSUMED,
            correlation_id=correlation_id,
        )
        self._log("seller_dashboard_link_consumed", grant.user, correlation_id)
        return DashboardAccessResult(status="CONSUMED", user=grant.user)

    @staticmethod
    def _hash(raw_token):
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _record_rejected(grant, correlation_id):
        record_audit_event(
            actor=grant.user,
            actor_type=AuditActorType.USER,
            entity_type=AuditEntityType.SELLER_DASHBOARD_ACCESS,
            entity_id=grant.id,
            event_type=AuditEventType.SELLER_DASHBOARD_ACCESS_REJECTED,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _log(event, user, correlation_id):
        logger.info(event, extra={"user_id": str(user.id) if user else None, "correlation_id": correlation_id})
