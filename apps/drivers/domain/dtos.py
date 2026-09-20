"""Channel-neutral, privacy-minimised delivery mission contracts."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class DriverMissionLocationDto:
    display_name: str | None
    address_label: str | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True, kw_only=True)
class DriverMissionDto:
    assignment_id: str
    order_id: str
    order_reference: str
    order_status: str
    pickup_store_id: str
    pickup: DriverMissionLocationDto
    destination: DriverMissionLocationDto
    coordinates_available: bool
    delivery_instructions: str | None
    allowed_actions: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class PickupConfirmationResult:
    mission: DriverMissionDto
    picked_up_at: object
    duplicate: bool


@dataclass(frozen=True, kw_only=True)
class StartDeliveryResult:
    mission: DriverMissionDto
    delivery_started_at: object
    duplicate: bool


@dataclass(frozen=True, kw_only=True)
class DeliveryConfirmationResult:
    mission: DriverMissionDto
    delivered_at: object
    duplicate: bool
