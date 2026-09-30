from django.urls import path

from .views import AgentCodeCheckView

urlpatterns = [
    path("check/", AgentCodeCheckView.as_view(), name="agent-check"),
]
