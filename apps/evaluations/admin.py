from django.contrib import admin

from apps.core.admin_tools import ReadOnlyAdmin

from .models import Evaluation


@admin.register(Evaluation)
class EvaluationAdmin(ReadOnlyAdmin):
    list_display = ['adaptation', 'autonomy_level', 'comprehension', 'status', 'record_status', 'updated_at']
    list_filter = ['status', 'autonomy_level', 'comprehension']
    exclude = ['observation', 'supports_note']
