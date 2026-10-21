from django.contrib import admin

from .models import OriginalMaterial, Subject


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'code']


@admin.register(OriginalMaterial)
class OriginalMaterialAdmin(admin.ModelAdmin):
    list_display = ['title', 'subject', 'input_type', 'status', 'created_at']
    list_filter = ['status', 'input_type', 'subject']
    readonly_fields = ['ai_analysis', 'ai_error']
