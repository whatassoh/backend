from rest_framework import serializers

from apps.identity.application.selectors import get_user_roles
from apps.identity.infrastructure.models import User


class CurrentUserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "whatsapp_phone",
            "first_name",
            "last_name",
            "preferred_language",
            "status",
            "roles",
        ]

    def get_roles(self, user: User) -> list[str]:
        return [user_role.role for user_role in get_user_roles(user)]
