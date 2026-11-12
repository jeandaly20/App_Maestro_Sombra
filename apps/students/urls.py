from django.urls import path

from . import views

urlpatterns = [
    path('', views.StudentListView.as_view(), name='student_list'),
    path('nuevo/', views.StudentCreateView.as_view(), name='student_create'),
    path('<uuid:pk>/editar/', views.StudentUpdateView.as_view(), name='student_edit'),
    path('<uuid:pk>/baja/', views.StudentDeactivateView.as_view(), name='student_deactivate'),
    path('<uuid:pk>/respaldo/', views.StudentDocumentView.as_view(), name='student_document'),
    path('<uuid:pk>/autorizacion/', views.StudentConsentView.as_view(), name='student_consent'),
    path('<uuid:pk>/expediente.zip', views.StudentFileView.as_view(), name='student_file'),
    path('autorizacion.pdf', views.StudentConsentFormView.as_view(), name='consent_form'),
    path('expedientes.pdf', views.StudentRecordsPdfView.as_view(), name='student_records_pdf'),
]
