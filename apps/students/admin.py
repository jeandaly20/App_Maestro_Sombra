from django.contrib import admin

from apps.core.admin_tools import ReadOnlyAdmin

from .models import Student


@admin.register(Student)
class StudentAdmin(ReadOnlyAdmin):
    # The administrator only sees pseudonyms: the real name belongs to the teacher (LOPDP)
    list_display = ['pseudonym', 'grade', 'conditions', 'diagnosis_source', 'record_status', 'created_at']
    list_filter = ['grade', 'record_status']
    exclude = ['name', 'notes', 'birth_date', 'diagnosis_consent_at', 'learning_strengths', 'learning_barriers',
               'pedagogical_supports']
