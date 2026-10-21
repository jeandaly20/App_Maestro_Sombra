from django.contrib import admin

from .models import Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    # The administrator only sees pseudonyms: the real name belongs to the teacher (LOPDP)
    list_display = ['pseudonym', 'grade', 'conditions', 'created_at']
    list_filter = ['grade']
    exclude = ['name', 'notes', 'birth_date']
