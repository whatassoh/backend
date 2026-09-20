"""Real PostgreSQL transition races for Phase 11 delivery actions."""
import threading

import pytest
from django.db import close_old_connections

from apps.drivers.application.delivery_execution import DeliveryExecutionService
from apps.drivers.infrastructure.models import DriverAssignment
from apps.drivers.tests.test_delivery_execution import assignment
from apps.identity.infrastructure.models import User
from apps.orders.domain.enums import OrderStatus
from apps.orders.infrastructure.models import Order


def race(action, assignment_id, user_id):
    barrier, results = threading.Barrier(2), []
    def worker():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            result = getattr(DeliveryExecutionService(), action)(User.objects.get(pk=user_id), assignment_id=assignment_id)
            results.append(result.duplicate)
        finally:
            close_old_connections()
    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads)
    return results


@pytest.mark.django_db(transaction=True)
def test_pickup_start_and_completion_duplicate_races_have_one_effective_transition():
    item, _ = assignment()
    assert sorted(race("confirm_pickup", item.id, item.driver.user_id)) == [False, True]
    item.order.refresh_from_db(); assert item.order.status == OrderStatus.PICKED_UP
    assert sorted(race("start_delivery", item.id, item.driver.user_id)) == [False, True]
    item.order.refresh_from_db(); assert item.order.status == OrderStatus.IN_DELIVERY
    assert sorted(race("confirm_delivered", item.id, item.driver.user_id)) == [False, True]
    item.order.refresh_from_db(); item.refresh_from_db()
    assert item.order.status == OrderStatus.DELIVERED and not item.is_active


@pytest.mark.django_db(transaction=True)
def test_delivered_vs_failure_race_has_one_terminal_winner():
    item, _ = assignment()
    service = DeliveryExecutionService()
    service.confirm_pickup(item.driver.user, assignment_id=item.id)
    service.start_delivery(item.driver.user, assignment_id=item.id)
    issue, _ = service.report_issue(item.driver.user, assignment_id=item.id, issue_type="DELIVERY_IMPOSSIBLE")
    barrier, outcomes = threading.Barrier(2), []
    def delivered():
        close_old_connections()
        try:
            barrier.wait(timeout=10); DeliveryExecutionService().confirm_delivered(User.objects.get(pk=item.driver.user_id), assignment_id=item.id); outcomes.append("DELIVERED")
        except Exception: outcomes.append("STALE")
        finally: close_old_connections()
    def failed():
        close_old_connections()
        try:
            barrier.wait(timeout=10); DeliveryExecutionService().confirm_failure(User.objects.get(pk=item.driver.user_id), assignment_id=item.id, issue_id=issue.id); outcomes.append("FAILED")
        except Exception: outcomes.append("STALE")
        finally: close_old_connections()
    threads = [threading.Thread(target=delivered), threading.Thread(target=failed)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads)
    item.order.refresh_from_db()
    assert item.order.status in {OrderStatus.DELIVERED, OrderStatus.FAILED}
    assert len([outcome for outcome in outcomes if outcome in {"DELIVERED", "FAILED"}]) == 1
