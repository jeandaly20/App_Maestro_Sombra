from django.urls import path

from . import views

urlpatterns = [
    path('', views.AdaptView.as_view(), name='adapt'),
    path('banco/', views.BankView.as_view(), name='bank'),
    path('clase/', views.LessonUploadView.as_view(), name='lesson_upload'),
    path('clase/<uuid:pk>/configurar/', views.LessonSetupView.as_view(), name='lesson_setup'),
    path('clase/<uuid:pk>/', views.LessonDetailView.as_view(), name='lesson_detail'),
    path('clase/<uuid:pk>/estado/', views.LessonStatusView.as_view(), name='lesson_status'),
    path('clase/<uuid:pk>/eliminar/', views.LessonDeleteView.as_view(), name='lesson_delete'),
    path('clase/adaptacion/<uuid:pk>/excel/', views.LessonExcelView.as_view(), name='lesson_excel'),
    path('material/<uuid:pk>/', views.MaterialDetailView.as_view(), name='material_detail'),
    path('material/<uuid:pk>/estado/', views.MaterialStatusView.as_view(), name='material_status'),
    path('material/<uuid:pk>/archivo/', views.MaterialFileView.as_view(), name='material_file'),
    path('material/<uuid:pk>/figura/<int:number>/', views.MaterialFigureView.as_view(), name='material_figure'),
    path('adaptacion/<uuid:pk>/accion/', views.AdaptationActionView.as_view(), name='adaptation_action'),
    path('adaptacion/<uuid:pk>/editar/', views.AdaptationEditView.as_view(), name='adaptation_edit'),
    path('adaptacion/<uuid:pk>/eliminar/', views.AdaptationDeleteView.as_view(), name='adaptation_delete'),
    path('adaptacion/<uuid:pk>/guia.pdf', views.AdaptationPdfView.as_view(), name='adaptation_pdf'),
]
