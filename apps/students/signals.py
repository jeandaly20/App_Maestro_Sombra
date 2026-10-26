from django.db.models.signals import post_delete
from django.dispatch import receiver

from .documents import delete_document
from .models import Student


@receiver(post_delete, sender=Student)
def delete_student_document(sender, instance, **kwargs):
    """Removing a student (or the teacher who owns it) also removes the diagnosis document from disk (LOPDP)."""
    delete_document(instance, save=False)
