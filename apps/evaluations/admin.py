from django.contrib import admin

from .models import Evaluation


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = ['adaptation', 'autonomy_level', 'comprehension', 'status', 'record_status', 'updated_at']
    list_filter = ['status', 'autonomy_level', 'comprehension']
    exclude = ['observation']
