from django.contrib import admin

from apps.core.admin_tools import NoDeleteAdmin, ReadOnlyAdmin

from .models import OriginalMaterial, Subject


@admin.register(Subject)
class SubjectAdmin(NoDeleteAdmin):
    list_display = ['name', 'code', 'record_status']


@admin.register(OriginalMaterial)
class OriginalMaterialAdmin(ReadOnlyAdmin):
    list_display = ['title', 'subject', 'input_type', 'status', 'record_status', 'created_at']
    list_filter = ['status', 'input_type', 'subject']
    readonly_fields = ['ai_analysis', 'ai_error']
