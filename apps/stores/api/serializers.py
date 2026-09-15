from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from rest_framework import serializers

from apps.stores.infrastructure.models import Store, StoreMembership


class StoreSerializer(serializers.ModelSerializer):
    latitude = serializers.FloatField(write_only=True)
    longitude = serializers.FloatField(write_only=True)
    location_latitude = serializers.SerializerMethodField()
    location_longitude = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = [
            "id",
            "name",
            "description",
            "status",
            "phone",
            "latitude",
            "longitude",
            "location_latitude",
            "location_longitude",
            "address_label",
            "city",
            "neighborhood",
            "service_radius_meters",
            "is_open",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "location_latitude",
            "location_longitude",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs: dict) -> dict:
        latitude = attrs.pop("latitude", None)
        longitude = attrs.pop("longitude", None)

        if self.instance is None and (latitude is None or longitude is None):
            raise serializers.ValidationError(
                "Latitude and longitude are required to create a store."
            )

        if latitude is not None or longitude is not None:
            if latitude is None or longitude is None:
                raise serializers.ValidationError(
                    "Latitude and longitude must be provided together."
                )
            attrs["location"] = Point(longitude, latitude, srid=4326)

        return attrs

    def get_location_latitude(self, store: Store) -> float:
        return store.location.y

    def get_location_longitude(self, store: Store) -> float:
        return store.location.x


class StoreMembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(),
        source="user",
        write_only=True,
    )
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = StoreMembership
        fields = [
            "id",
            "store",
            "user",
            "user_id",
            "role",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "store",
            "user",
            "created_at",
            "updated_at",
        ]
