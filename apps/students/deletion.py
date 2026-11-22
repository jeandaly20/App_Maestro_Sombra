"""
Permanent deletion of a student (LOPDP, art. 15, right to deletion; HU-28): the record and everything about
them in the app goes, with their stored files. The official record (DIAC, DECE file, grades) is the school's and
lives in its own systems: this app is the teacher's support tool, so nothing has to stay here.

What goes: the student (name, birth date, NEE, notes, profile), their worksheets with every version and PDF,
their adapted classes, the classroom evaluations and recommendation reviews, the consent records with their
signed files, and the diagnosis document. What stays: the teacher's own materials and class plans, which hold no
data of the student (names are removed before anything is stored or sent to the AI).
"""
import logging
from dataclasses import dataclass

from django.core.files.storage import default_storage
from django.db import transaction

from apps.adaptations.models import CurricularAdaptation, LessonAdaptation
from apps.evaluations.models import Evaluation

logger = logging.getLogger('maestro_sombra')


@dataclass
class DeletionSummary:
    """What will be deleted, for the confirmation page."""
    worksheets: int
    classes: int
    evaluations: int
    consents: int
    has_document: bool


def summary(student):
    return DeletionSummary(
        worksheets=CurricularAdaptation.objects.filter(student=student).count(),
        classes=LessonAdaptation.objects.filter(student=student).count(),
        evaluations=Evaluation.objects.filter(adaptation__student=student).count(),
        consents=student.consents.count(),
        has_document=bool(student.diagnosis_file or student.consent_file),
    )


def stored_files(student):
    """Every file of the student in storage: documents, signed authorizations and worksheet PDFs."""
    paths = [student.diagnosis_file, student.consent_file]
    paths += list(student.consents.values_list('signed_file', flat=True))
    paths += [adaptation.pdf.name for adaptation in CurricularAdaptation.objects.filter(student=student)
              if adaptation.pdf]
    return sorted({path for path in paths if path})


def delete_permanently(student):
    """
    Deletes the student and everything about them. The rows go in one transaction; the files are deleted only
    after it commits, so a failure never leaves records pointing to missing files.
    """
    pseudonym, paths = student.pseudonym, stored_files(student)
    with transaction.atomic():
        # Their rows: worksheets (and their evaluations), classes, consents and reviews go with the student
        student.delete()
        transaction.on_commit(lambda: [default_storage.delete(path) for path in paths
                                       if default_storage.exists(path)])
    logger.info('Student permanently deleted: %s (%s files)', pseudonym, len(paths))
