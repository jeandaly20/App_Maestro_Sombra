from django.urls import path

from . import views

urlpatterns = [
    path('', views.AdaptView.as_view(), name='adapt'),
    path('banco/', views.BankView.as_view(), name='bank'),
    path('material/<uuid:pk>/', views.MaterialDetailView.as_view(), name='material_detail'),
    path('material/<uuid:pk>/estado/', views.MaterialStatusView.as_view(), name='material_status'),
    path('material/<uuid:pk>/archivo/', views.MaterialFileView.as_view(), name='material_file'),
    path('material/<uuid:pk>/figura/<int:number>/', views.MaterialFigureView.as_view(), name='material_figure'),
    path('adaptacion/<uuid:pk>/accion/', views.AdaptationActionView.as_view(), name='adaptation_action'),
    path('adaptacion/<uuid:pk>/editar/', views.AdaptationEditView.as_view(), name='adaptation_edit'),
    path('adaptacion/<uuid:pk>/guia.pdf', views.AdaptationPdfView.as_view(), name='adaptation_pdf'),
]
