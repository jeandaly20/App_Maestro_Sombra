from django.contrib import admin

from .models import Evaluacion


@admin.register(Evaluacion)
class EvaluacionAdmin(admin.ModelAdmin):
    list_display = ['adaptacion', 'nivel_autonomia', 'comprension', 'estado', 'actualizado']
    list_filter = ['estado', 'nivel_autonomia', 'comprension']
    exclude = ['observacion']
