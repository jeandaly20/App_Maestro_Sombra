from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.materials.models import OriginalMaterial

from .figures import delete_figures


@receiver(post_delete, sender=OriginalMaterial)
def delete_material_files(sender, instance, **kwargs):
    """
    Deleting a material (or the teacher who owns it) also removes the uploaded worksheet
    and its cropped figures from disk, so no copy of the material is left behind (LOPDP).
    """
    if instance.file:
        instance.file.delete(save=False)
    delete_figures(instance)
