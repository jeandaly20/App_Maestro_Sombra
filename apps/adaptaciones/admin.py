from django.contrib import admin

from .models import AdaptacionCurricular


@admin.register(AdaptacionCurricular)
class AdaptacionCurricularAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'titulo', 'condicion', 'version', 'estado', 'modelo_ia', 'creado']
    list_filter = ['estado', 'condicion', 'modelo_ia']
    search_fields = ['codigo', 'titulo']
