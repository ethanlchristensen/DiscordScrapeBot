from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Message
from .serializers import MessageSerializer
from rest_framework.pagination import PageNumberPagination
from django_tables2 import SingleTableView
from django.views.generic import TemplateView
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils.decorators import method_decorator
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils.decorators import method_decorator
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.views import redirect_to_login
from django.urls import reverse

from .tables import MessageTable


def is_admin(user):
    return user.is_authenticated and user.is_staff


class CustomPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 1000


class MessageViewSet(viewsets.ModelViewSet):
    queryset = Message.objects.all().order_by("-created_at")
    serializer_class = MessageSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        queryset = Message.objects.all().order_by("-created_at")
        channel_id = self.request.query_params.get("channel_id", None)
        if channel_id is not None:
            queryset = queryset.filter(channel_id=channel_id)
        return queryset


@method_decorator(login_required, name="dispatch")
class MessageListView(SingleTableView):
    model = Message
    table_class = MessageTable
    template_name = "messages_list.html"

    @method_decorator(user_passes_test(is_admin, login_url="/admin"))
    def dispatch(self, *args, **kwargs):
        if not self.request.user.is_staff:
            return redirect("/admin")
        return super().dispatch(*args, **kwargs)
