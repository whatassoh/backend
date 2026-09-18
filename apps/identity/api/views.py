from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity.api.serializers import CurrentUserSerializer


class MeView(APIView):
    """Authenticated identity endpoint."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Identity"],
        summary="Obtenir l'utilisateur courant",
        description="Retourne le profil de l'utilisateur authentifie par JWT.",
        responses={200: CurrentUserSerializer},
    )
    def get(self, request):
        serializer = CurrentUserSerializer(request.user)

        return Response(serializer.data)
