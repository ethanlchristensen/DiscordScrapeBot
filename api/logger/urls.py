from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MessageViewSet, MessageListView

router = DefaultRouter()
router.register(r'messages', MessageViewSet)

urlpatterns = [
    path('api/', include(router.urls)),
    path('messages/', MessageListView.as_view(), name='message-list'),
]