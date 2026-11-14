from django.urls import path

from . import views

urlpatterns = [
    path('', views.StudentListView.as_view(), name='student_list'),
    path('nuevo/', views.StudentCreateView.as_view(), name='student_create'),
    path('<uuid:pk>/editar/', views.StudentUpdateView.as_view(), name='student_edit'),
    path('<uuid:pk>/historial/', views.StudentProgressView.as_view(), name='student_progress'),
    path('<uuid:pk>/historial/revisar/', views.StudentRecommendationReviewView.as_view(), name='student_recommendation_review'),
    path('<uuid:pk>/baja/', views.StudentDeactivateView.as_view(), name='student_deactivate'),
    path('<uuid:pk>/respaldo/', views.StudentDocumentView.as_view(), name='student_document'),
    path('<uuid:pk>/autorizacion/', views.StudentConsentView.as_view(), name='student_consent'),
    path('<uuid:pk>/expediente.zip', views.StudentFileView.as_view(), name='student_file'),
    path('<uuid:pk>/autorizaciones/nueva/', views.StudentConsentCreateView.as_view(), name='student_consent_create'),
    path('<uuid:pk>/autorizaciones/<uuid:consent_pk>/revocar/', views.StudentConsentRevokeView.as_view(),
         name='student_consent_revoke'),
    path('<uuid:pk>/autorizaciones/<uuid:consent_pk>/documento/', views.StudentConsentFileView.as_view(),
         name='student_consent_record_file'),
    path('autorizacion.pdf', views.StudentConsentFormView.as_view(), name='consent_form'),
    path('expedientes.pdf', views.StudentRecordsPdfView.as_view(), name='student_records_pdf'),
]
