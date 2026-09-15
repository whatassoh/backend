from django.urls import path

from apps.identity.api.views import MeView

urlpatterns = [
    path("me/", MeView.as_view(), name="identity-me"),
]
