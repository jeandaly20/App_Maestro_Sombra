from django.contrib import admin

from apps.core.admin_tools import ReadOnlyAdmin

from .models import CurricularAdaptation


@admin.register(CurricularAdaptation)
class CurricularAdaptationAdmin(ReadOnlyAdmin):
    list_display = ['code', 'title', 'condition', 'version', 'status', 'record_status', 'ai_model', 'created_at']
    list_filter = ['status', 'condition', 'ai_model']
    search_fields = ['code', 'title']
