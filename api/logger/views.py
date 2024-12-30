from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Message
from .serializers import MessageSerializer

class MessageViewSet(viewsets.ModelViewSet):
    queryset = Message.objects.all()
    serializer_class = MessageSerializer
    # permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Message.objects.all()
        channel_id = self.request.query_params.get('channel_id', None)
        if channel_id is not None:
            queryset = queryset.filter(channel_id=channel_id)
        return queryset