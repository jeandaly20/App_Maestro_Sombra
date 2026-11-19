from django.urls import path

from . import views

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('historial-grupo/', views.GroupProgressView.as_view(), name='group_progress'),
]
