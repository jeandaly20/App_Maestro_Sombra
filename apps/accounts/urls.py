from django.urls import path

from . import views

# Paths are in Spanish because the user sees them in the address bar
urlpatterns = [
    path('ingresar/', views.LoginView.as_view(), name='login'),
    path('salir/', views.LogoutView.as_view(), name='logout'),
    path('registro/', views.RegisterView.as_view(), name='register'),
    path('terminos/', views.TermsView.as_view(), name='terms'),
    path('recuperar/', views.PasswordResetView.as_view(), name='password_reset'),
    path('recuperar/enviado/', views.PasswordResetSentView.as_view(), name='password_reset_sent'),
    path('recuperar/<uidb64>/<token>/', views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]
