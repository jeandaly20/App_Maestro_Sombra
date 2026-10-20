from django.contrib import admin

from .models import Materia, MaterialOriginal


@admin.register(Materia)
class MateriaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'abreviatura']


@admin.register(MaterialOriginal)
class MaterialOriginalAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'materia', 'tipo_entrada', 'estado', 'creado']
    list_filter = ['estado', 'tipo_entrada', 'materia']
    readonly_fields = ['analisis_ia', 'error_ia']
