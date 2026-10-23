from django.contrib import admin

from .models import CurricularAdaptation


@admin.register(CurricularAdaptation)
class CurricularAdaptationAdmin(admin.ModelAdmin):
    list_display = ['code', 'title', 'condition', 'version', 'status', 'record_status', 'ai_model', 'created_at']
    list_filter = ['status', 'condition', 'ai_model']
    search_fields = ['code', 'title']
