from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Message
from .serializers import MessageSerializer
from rest_framework.pagination import PageNumberPagination
from django_tables2 import SingleTableView
from django.views.generic import TemplateView
from .tables import MessageTable


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
    

class MessageListView(SingleTableView):
    model = Message
    table_class = MessageTable
    template_name = "messages_list.html"