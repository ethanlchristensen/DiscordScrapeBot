from .models import *
from .serializers import *
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

class DiscordMessageViewSet(viewsets.ModelViewSet):
    queryset = DiscordMessage.objects.all()
    serializer_class = DiscordMessageSerializer
    lookup_field = 'message_id'

    @action(detail=True, methods=['post'])
    def edit(self, request, message_id=None):
        message = self.get_object()
        
        # Create edit history
        MessageEdit.objects.create(
            message=message,
            previous_content=message.content
        )
        
        # Update message content
        message.content = request.data.get('content', message.content)
        message.save()
        
        return Response(self.get_serializer(message).data)