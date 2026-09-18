from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.stores.api.serializers import StoreMembershipSerializer, StoreSerializer
from apps.stores.application.permissions import can_manage_store, is_store_owner
from apps.stores.application.services import (
    add_store_member,
    change_store_member_role,
    create_store,
    remove_store_member,
)
from apps.stores.infrastructure.models import Store, StoreMembership


@extend_schema(
    tags=["Stores"],
    description=(
        "Gestion des boutiques et de leurs membres. Les ecritures exigent "
        "les permissions de gestion de boutique."
    ),
)
class StoreViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = StoreSerializer
    queryset = Store.objects.all().prefetch_related("memberships")
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer: StoreSerializer) -> None:
        serializer.instance = create_store(
            created_by=self.request.user,
            **serializer.validated_data,
        )

    def perform_update(self, serializer: StoreSerializer) -> None:
        store = self.get_object()

        if not can_manage_store(self.request.user, store):
            self.permission_denied(self.request)

        serializer.save()

    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, pk=None):
        store = self.get_object()

        if request.method == "GET":
            memberships = StoreMembership.objects.filter(store=store).select_related(
                "user"
            )
            serializer = StoreMembershipSerializer(memberships, many=True)

            return Response(serializer.data)

        if not can_manage_store(request.user, store):
            self.permission_denied(request)

        serializer = StoreMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = add_store_member(
            store,
            serializer.validated_data["user"],
            serializer.validated_data["role"],
        )

        return Response(
            StoreMembershipSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"members/(?P<membership_id>[^/.]+)",
    )
    def member_detail(self, request, pk=None, membership_id=None):
        store = self.get_object()
        membership = StoreMembership.objects.get(id=membership_id, store=store)

        if request.method == "DELETE":
            if not is_store_owner(request.user, store):
                self.permission_denied(request)

            remove_store_member(store, membership.user)

            return Response(status=status.HTTP_204_NO_CONTENT)

        if not is_store_owner(request.user, store):
            self.permission_denied(request)

        serializer = StoreMembershipSerializer(
            membership,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        updated_membership = change_store_member_role(
            store,
            membership.user,
            serializer.validated_data["role"],
        )

        return Response(StoreMembershipSerializer(updated_membership).data)
