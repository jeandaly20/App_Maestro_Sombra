"""
What the administrator may do in /admin/. Nothing is ever deleted from the panel: a record is deactivated with
record_status, so the history stays and no stored file is left behind (permanent deletion of a student, with its
files, belongs to their teacher: apps.students.deletion). The teachers' work (students, materials, worksheets,
evaluations) is only consulted: the administrator never edits what a teacher reviewed and approved.
"""
from django.contrib import admin


class NoDeleteAdmin(admin.ModelAdmin):
    """Records are deactivated (record_status), never deleted from the panel."""

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(NoDeleteAdmin):
    """Only consulted: no adding, changing or deleting from the panel."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
