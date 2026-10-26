"""
Diagnosis backing document (UDAI report, MSP certificate...). Optional and protected (LOPDP, ISO/IEC 27701):

- Only kept with the legal representative's authorization (the date is stored).
- Encrypted on disk with the same Fernet key as the other sensitive fields; the original name is not kept.
- Served only to the student's teacher, every opening is logged, and it is never sent to the AI.
- Deleted when the student is given leave or removed.
- Read locally (never by the AI) to flag, not to prove, whether it matches the student: their name, the
  declared institution and one of their conditions.
"""
import io
import logging
import re
import unicodedata
import uuid

import pypdfium2 as pdfium

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.core.fields import decrypt_bytes, encrypt_bytes

logger = logging.getLogger('maestro_sombra')

MAX_DOCUMENT_MB = 5
ACCEPT = 'application/pdf,image/jpeg,image/png'

# Real file type from its first bytes: the extension or the browser's word is not trusted
SIGNATURES = {
    b'%PDF-': 'application/pdf',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'\xff\xd8\xff': 'image/jpeg',
}
EXTENSIONS = {'application/pdf': 'pdf', 'image/png': 'png', 'image/jpeg': 'jpg'}


def detect_type(head):
    return next((kind for signature, kind in SIGNATURES.items() if head.startswith(signature)), None)


def save_document(student, uploaded, content_type):
    """Encrypts and stores the file on the (not yet saved) student; returns the replaced path to delete."""
    uploaded.seek(0)
    content = uploaded.read()
    path = default_storage.save(f'students/{student.teacher_id}/{student.pk}/respaldo-{uuid.uuid4().hex}.bin',
                                ContentFile(encrypt_bytes(content)))
    old_path = student.diagnosis_file
    student.diagnosis_file = path
    student.diagnosis_file_type = content_type
    student.diagnosis_consent_at = timezone.now()
    student.diagnosis_check = check_document(student, content, content_type)
    logger.info('Diagnosis document stored for %s', student.pseudonym)
    return old_path


# Words the declared institution writes on its documents
INSTITUTION_WORDS = {
    'UDAI_REPORT': ['udai', 'unidad distrital de apoyo'],
    'MSP_CERTIFICATE': ['ministerio de salud', 'msp', 'calificacion de discapacidad'],
    'DECE_REPORT': ['dece', 'consejeria estudiantil'],
    'CLINICAL_REPORT': ['psicolog', 'neurolog', 'psiquiatr', 'medico', 'medica'],
}
CONDITION_WORDS = {
    'DYSLEXIA': ['dislexia'],
    'DYSCALCULIA': ['discalculia'],
    'ADHD': ['tdah', 'deficit de atencion', 'hiperactividad'],
    'ASD': ['autismo', 'autista', 'tea', 'espectro autista'],
    'ID_MILD': ['discapacidad intelectual', 'intelectual'],
    'ID_MODERATE': ['discapacidad intelectual', 'intelectual'],
}
MAX_PAGES_READ = 5


def plain(text):
    """Lowercase without accents, so 'Benítez' and 'BENITEZ' match."""
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def has_word(text, word):
    # Whole words for short ones (TEA, MSP); prefixes such as "psicolog" match any ending
    if len(word) <= 5:
        return re.search(rf'\b{re.escape(word)}\b', text) is not None
    return word in text


def pdf_text(content):
    try:
        pdf = pdfium.PdfDocument(io.BytesIO(content))
    except pdfium.PdfiumError:
        return ''
    try:
        return ' '.join(pdf[i].get_textpage().get_text_bounded() for i in range(min(len(pdf), MAX_PAGES_READ)))
    finally:
        pdf.close()


def check_document(student, content, content_type):
    """Local, text-only check of an attached document. Images and scans cannot be read without OCR."""
    text = plain(pdf_text(content)) if content_type == 'application/pdf' else ''
    if len(text.strip()) < 20:
        return {'readable': False}
    names = [word for word in plain(student.name).split() if len(word) > 2]
    found = [word for word in names if has_word(text, word)]
    return {
        'readable': True,
        # First name plus at least one surname (or the only word, for a single-word name)
        'name': bool(names) and names[0] in found and (len(names) == 1 or len(found) >= 2),
        'institution': any(has_word(text, word) for word in INSTITUTION_WORDS.get(student.diagnosis_source, [])),
        'diagnosis': any(has_word(text, word) for condition in student.conditions
                         for word in CONDITION_WORDS.get(condition, [])),
    }


def recheck_document(student):
    """The name, conditions or source changed: read the stored document again."""
    if student.has_document:
        student.diagnosis_check = check_document(student, read_document(student), student.diagnosis_file_type)


def read_document(student):
    with default_storage.open(student.diagnosis_file, 'rb') as stored:
        return decrypt_bytes(stored.read())


def delete_document(student, *, save=True):
    if not student.diagnosis_file:
        return
    default_storage.delete(student.diagnosis_file)
    student.diagnosis_file = ''
    student.diagnosis_file_type = ''
    student.diagnosis_consent_at = None
    student.diagnosis_check = None
    if save:
        student.save(update_fields=['diagnosis_file', 'diagnosis_file_type', 'diagnosis_consent_at',
                                    'diagnosis_check', 'updated_at'])
    logger.info('Diagnosis document deleted for %s', student.pseudonym)


def download_name(student):
    return f'respaldo-{student.pseudonym}.{EXTENSIONS.get(student.diagnosis_file_type, "bin")}'
