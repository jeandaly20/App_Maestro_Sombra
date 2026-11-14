from django.urls import path

from . import views

urlpatterns = [
    path('<uuid:pk>/', views.EvaluationView.as_view(), name='evaluation'),
    path('<uuid:pk>/recomendacion-revisada/', views.EvaluationRecommendationReviewView.as_view(),
         name='evaluation_recommendation_review'),
]
