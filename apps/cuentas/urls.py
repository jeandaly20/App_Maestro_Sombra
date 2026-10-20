from django.urls import path

from . import views

urlpatterns = [
    path('ingresar/', views.IngresoView.as_view(), name='login'),
    path('salir/', views.SalidaView.as_view(), name='salir'),
    path('registro/', views.RegistroView.as_view(), name='registro'),
    path('terminos/', views.TerminosView.as_view(), name='terminos'),
    path('recuperar/', views.RecuperarView.as_view(), name='recuperar'),
    path('recuperar/enviado/', views.RecuperarEnviadoView.as_view(), name='recuperar_enviado'),
    path('recuperar/<uidb64>/<token>/', views.NuevaClaveView.as_view(), name='nueva_clave'),
]
