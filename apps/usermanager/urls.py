from django.urls import path

from .timezone_views import TimezoneDashboardView
from .views import UserCRUDView, send_user_link, unlock_user, user_stat_detail

urlpatterns = [
    path("manage/users/timezones/", TimezoneDashboardView.as_view(), name="manage/users-timezones"),
    path("manage/users/stats/<str:stat_type>/", user_stat_detail, name="manage/users-stat-detail"),
    path("manage/users/<int:pk>/send-link/", send_user_link, name="manage/users-send-link"),
    path("manage/users/<int:pk>/unlock/", unlock_user, name="manage/users-unlock"),
    *UserCRUDView.get_urls(),
]
