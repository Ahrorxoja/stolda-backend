"""Ro'yxatdan o'tishda agent kodini tekshirish."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import find_agent


class AgentCodeCheckView(APIView):
    """`GET /api/agents/check/?code=ALI25` → `{"valid": true, "name": "Ali"}`.

    Faqat ism (familiyasiz) — kodni bilgan odam agent haqida boshqa narsa bilmasin.
    """

    permission_classes = (IsAuthenticated,)
    throttle_scope = "agent_check"

    def get(self, request):
        agent = find_agent(request.query_params.get("code", ""))
        if agent is None:
            return Response({"valid": False, "name": ""})
        return Response({"valid": True, "name": agent.name.split()[0], "code": agent.code})
