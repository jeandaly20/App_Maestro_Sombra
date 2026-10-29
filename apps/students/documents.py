"""
Diagnosis backing document (UDAI report, MSP certificate...). Optional and protected (LOPDP, ISO/IEC 27701):

- Only kept with the legal representative's authorization (the date is stored).
- Encrypted on disk with the same Fernet key as the other sensitive fields; the original name is not kept.
- Served only to the student's teacher, every opening is logged, and it is never sent to the AI.
- Deleted when the student is given leave or removed.
- Read locally (never by the AI) to flag, not to prove, whether it matches the student: their name, the
  declared institution and one of their conditions. Digital PDFs give their text directly; photos and
  scanned PDFs are read with Tesseract OCR on the same server.
"""
import functools
import io
import logging
import os
import re
import shutil
import subprocess
import unicodedata
import uuid
from pathlib import Path

import pypdfium2 as pdfium
from django.conf import settings
from PIL import Image

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
OCR_PAGES = 3           # scanned PDFs: only the first pages, where the name and diagnosis usually are
OCR_TIMEOUT_SECONDS = 40
OCR_MAX_SIDE = 2500     # bigger photos are reduced: faster, and enough for printed text
WINDOWS_TESSERACT = Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe')
USER_TESSDATA = Path(os.getenv('LOCALAPPDATA', '')) / 'Tesseract-OCR' / 'tessdata'


@functools.lru_cache(maxsize=1)
def tesseract():
    """(command plus its options, language) of the local Tesseract, or None if it is not installed."""
    command = settings.TESSERACT_CMD or shutil.which('tesseract') or (
        str(WINDOWS_TESSERACT) if WINDOWS_TESSERACT.exists() else '')
    if not command:
        return None
    data_dir = settings.TESSERACT_DATA_DIR or (
        str(USER_TESSDATA) if (USER_TESSDATA / 'spa.traineddata').exists() else '')
    base = [command] + (['--tessdata-dir', data_dir] if data_dir else [])
    try:
        listed = subprocess.run(base + ['--list-langs'], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    languages = set(listed.split())
    # Spanish when installed; English also reads Spanish text well enough, accents are ignored when comparing
    language = 'spa' if 'spa' in languages else 'eng' if 'eng' in languages else None
    return (base, language) if language else None


def ocr_image(image):
    """Text of a PIL image read by the local Tesseract ('' if it cannot)."""
    found = tesseract()
    if not found:
        return ''
    image = image.convert('L')
    image.thumbnail((OCR_MAX_SIDE, OCR_MAX_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    base, language = found
    try:
        # stdin/stdout: the image never touches a temporary file on disk
        result = subprocess.run(base + ['stdin', 'stdout', '-l', language], input=buffer.getvalue(),
                                capture_output=True, timeout=OCR_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        logger.warning('OCR failed or timed out')
        return ''
    return result.stdout.decode('utf-8', errors='ignore')


def scanned_pdf_text(content):
    try:
        pdf = pdfium.PdfDocument(io.BytesIO(content))
    except pdfium.PdfiumError:
        return ''
    try:
        return ' '.join(ocr_image(pdf[i].render(scale=2.5).to_pil()) for i in range(min(len(pdf), OCR_PAGES)))
    finally:
        pdf.close()


def document_text(content, content_type):
    """(text, method): the PDF's own text, or OCR for photos and scans."""
    if content_type == 'application/pdf':
        text = pdf_text(content)
        if len(text.strip()) >= 20:
            return text, 'text'
        return scanned_pdf_text(content), 'ocr'
    try:
        return ocr_image(Image.open(io.BytesIO(content))), 'ocr'
    except OSError:
        return '', 'ocr'


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
    """Local check of an attached document: its text (digital PDF) or what OCR reads (photo, scan)."""
    raw, method = document_text(content, content_type)
    text = plain(raw)
    if len(text.strip()) < 20:
        return {'readable': False, 'method': method}
    names = [word for word in plain(student.name).split() if len(word) > 2]
    found = [word for word in names if has_word(text, word)]
    return {
        'readable': True,
        'method': method,
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
