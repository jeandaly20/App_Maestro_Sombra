from django.urls import path

from . import views

urlpatterns = [
    path('<uuid:pk>/', views.EvaluationView.as_view(), name='evaluation'),
]
