from django.urls import path

from . import views

urlpatterns = [
    path('', views.StudentCreateView.as_view(), name='student_list'),
    path('<uuid:pk>/editar/', views.StudentUpdateView.as_view(), name='student_edit'),
    path('<uuid:pk>/baja/', views.StudentDeactivateView.as_view(), name='student_deactivate'),
    path('expedientes.pdf', views.StudentRecordsPdfView.as_view(), name='student_records_pdf'),
]
