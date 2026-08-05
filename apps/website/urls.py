"""Website URL patterns.

The starter marketing pages were removed for CW Station — `/` is the CW
Monitor (via redirect so every `{% url 'website:home' %}` reference still
works), and the public search page stays.
"""

from django.urls import path

from . import views

app_name = "website"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("search/", views.search_view, name="search"),
]
