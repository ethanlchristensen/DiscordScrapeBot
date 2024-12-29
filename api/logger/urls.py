from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import DiscordMessageViewSet

router = DefaultRouter()
router.register(r'messages', DiscordMessageViewSet)

urlpatterns = router.urls