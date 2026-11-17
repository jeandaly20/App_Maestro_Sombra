"""
Diagnosis backing document (UDAI report, MSP certificate...). Optional and protected (LOPDP, ISO/IEC 27701):

- Only kept with the legal representative's authorization (the date is stored).
- Encrypted on disk with the same Fernet key as the other sensitive fields; the original name is not kept.
- Served only to the student's teacher, every opening is logged, and it is never sent to the AI.
- Deleted when the student is given leave or removed.
- Read locally (never by the AI) to check that it matches the student: their name, the declared institution
  and one of their conditions; the registration is not saved otherwise. Digital PDFs give their text
  directly; photos and scanned PDFs are read with Tesseract OCR on the same server.
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
from collections import namedtuple
from pathlib import Path

import pypdfium2 as pdfium
from django.conf import settings
from PIL import Image, ImageOps

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


def _store(student, content, prefix):
    return default_storage.save(f'students/{student.teacher_id}/{student.pk}/{prefix}-{uuid.uuid4().hex}.bin',
                                ContentFile(encrypt_bytes(content)))


def save_document(student, uploaded, content_type, check=None):
    """
    Encrypts and stores the file on the (not yet saved) student; returns the replaced path to delete.
    `check` is the reading the form already did (the form refuses a document that does not match).
    """
    uploaded.seek(0)
    content = uploaded.read()
    old_path = student.diagnosis_file
    student.diagnosis_file = _store(student, content, 'respaldo')
    student.diagnosis_file_type = content_type
    student.diagnosis_check = check or check_document(student, content, content_type)
    logger.info('Diagnosis document stored for %s', student.pseudonym)
    return old_path


def save_consent(student, uploaded, content_type):
    """
    The representative's signed authorization (the app's form, photographed or scanned), encrypted like the
    document. Its date is the authorization date. Returns the replaced path to delete. It is not read by OCR:
    the signature cannot be verified by software, the teacher checks it.
    """
    uploaded.seek(0)
    old_path = student.consent_file
    student.consent_file = _store(student, uploaded.read(), 'autorizacion')
    student.consent_file_type = content_type
    student.diagnosis_consent_at = timezone.now()
    logger.info('Signed authorization stored for %s', student.pseudonym)
    return old_path


# Words the declared institution writes on its documents
INSTITUTION_WORDS = {
    # UDAI reports carry "UDAI" in their code (MINEDUC-CZ5-…-UDAI-2020-…) and the national directorate in the header
    'UDAI_REPORT': ['udai', 'unidad distrital de apoyo', 'educacion especializada e inclusiva'],
    # The MSP certificate, or since 2025 the cédula issued by the Registro Civil with the disability recorded
    'MSP_CERTIFICATE': ['ministerio de salud', 'msp', 'calificacion de discapacidad', 'calificacion de la discapacidad',
                        'registro civil'],
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
# The issuing institution is in the heading: a "remitted by DECE" line further down does not make a DECE report
HEADER_CHARS = 400
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


def prepared(image):
    """What OCR reads: upright (phone photos keep their rotation in EXIF), grey and at most OCR_MAX_SIDE."""
    image = ImageOps.exif_transpose(image).convert('L')
    image.thumbnail((OCR_MAX_SIDE, OCR_MAX_SIDE))
    return image


def run_ocr(image, *options):
    """Tesseract's output for a prepared image: '' if it fails, None if Tesseract is not installed."""
    found = tesseract()
    if not found:
        return None
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    base, language = found
    try:
        # stdin/stdout: the image never touches a temporary file on disk
        result = subprocess.run(base + ['stdin', 'stdout', '-l', language, *options], input=buffer.getvalue(),
                                capture_output=True, timeout=OCR_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        logger.warning('OCR failed or timed out')
        return ''
    return result.stdout.decode('utf-8', errors='ignore')


def ocr_image(image):
    """Text of a PIL image read by the local Tesseract ('' if it cannot)."""
    return run_ocr(prepared(image)) or ''


Word = namedtuple('Word', 'text line left top right bottom')


def ocr_words(image):
    """The words of a prepared image with their boxes in pixels (Tesseract TSV); None if OCR is not installed."""
    # The option itself, not the 'tsv' config file: a tessdata folder with only the language has no configs
    output = run_ocr(image, '-c', 'tessedit_create_tsv=1')
    if output is None:
        return None
    words = []
    for row in output.splitlines()[1:]:
        cells = row.split('	')
        # level 5 = word; block, paragraph and line numbers say which printed line it belongs to
        if len(cells) == 12 and cells[0] == '5' and cells[11].strip():
            left, top, width, height = map(int, cells[6:10])
            words.append(Word(cells[11].strip(), tuple(cells[2:5]), left, top, left + width, top + height))
    return words


def first_page_image(content, content_type):
    """The first page of a PDF or the photo, prepared for OCR; None if the file cannot be opened."""
    try:
        if content_type == 'application/pdf':
            pdf = pdfium.PdfDocument(io.BytesIO(content))
            try:
                image = pdf[0].render(scale=2.5).to_pil()
            finally:
                pdf.close()
        else:
            image = Image.open(io.BytesIO(content))
            image.load()
    except (pdfium.PdfiumError, OSError, IndexError):
        return None
    return prepared(image)


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


def name_found(text, name):
    """The first name plus at least one surname (or the only word, for a single-word name) are in the text."""
    names = [word for word in plain(name).split() if len(word) > 2]
    found = [word for word in names if has_word(text, word)]
    return bool(names) and names[0] in found and (len(names) == 1 or len(found) >= 2)


def check_document(student, content, content_type):
    """Local check of an attached document: its text (digital PDF) or what OCR reads (photo, scan)."""
    raw, method = document_text(content, content_type)
    text = ' '.join(plain(raw).split())
    if len(text) < 20:
        return {'readable': False, 'method': method}
    return {
        'readable': True,
        'method': method,
        'name': name_found(text, student.name),
        'institution': any(has_word(text[:HEADER_CHARS], word)
                           for word in INSTITUTION_WORDS.get(student.diagnosis_source, [])),
        'diagnosis': any(has_word(text, word) for condition in student.conditions
                         for word in CONDITION_WORDS.get(condition, [])),
    }


CHECK_LABELS = (('name', 'el nombre del estudiante'), ('institution', 'la institución del tipo de documento elegido'),
                ('diagnosis', 'su condición NEE'))


def listed(items):
    return ' y '.join([', '.join(items[:-1]), items[-1]]) if len(items) > 1 else items[0]


def document_error(check, name):
    """Why the document cannot back this student, or None when its name, institution and NEE match."""
    if not check.get('readable'):
        if check.get('method') == 'ocr' and tesseract() is None:
            return 'Este servidor no puede leer fotos ni escaneos (OCR no instalado): sube el PDF digital del documento.'
        return ('No se pudo leer el documento (foto borrosa, oscura o escaneo de baja calidad). Sube una foto nítida, '
                'de frente y con buena luz, o el PDF original.')
    missing = [label for key, label in CHECK_LABELS if not check.get(key)]
    if not missing:
        return None
    return (f'El documento no coincide con el estudiante: no se encontró {listed(missing)}. Verifica que sea el '
            f'documento de {name}, que el tipo de documento elegido sea el correcto y que las NEE marcadas sean las '
            'del informe.')


def read_document(student):
    with default_storage.open(student.diagnosis_file, 'rb') as stored:
        return decrypt_bytes(stored.read())


def read_consent(student):
    with default_storage.open(student.consent_file, 'rb') as stored:
        return decrypt_bytes(stored.read())


DOCUMENT_FIELDS = ['diagnosis_file', 'diagnosis_file_type', 'diagnosis_consent_at', 'diagnosis_check',
                   'consent_file', 'consent_file_type']


def close_consents(student, *, paths=None, delete_files=False):
    """
    Consent records whose signed file goes (paths), or all of the student's (paths=None, the student's leave):
    they stop being in force, dated; with delete_files their own encrypted files are deleted too (LOPDP).
    """
    from .models import ConsentStatus, StudentConsent  # documents.py is imported by the models' users

    consents = StudentConsent.objects.filter(student_id=student.pk, status=ConsentStatus.ACTIVE)
    if paths is not None:
        consents = consents.filter(signed_file__in=[path for path in paths if path])
    for consent in consents:
        if delete_files and consent.signed_file and consent.signed_file != student.consent_file:
            default_storage.delete(consent.signed_file)
        consent.status = ConsentStatus.REVOKED
        consent.revoked_at = timezone.now()
        consent.signed_file = ''
        consent.save(update_fields=['status', 'revoked_at', 'signed_file', 'updated_at'])


def save_ai_consent_file(student, uploaded):
    """The signed file of a new authorization, encrypted like the other documents; returns its storage path."""
    uploaded.seek(0)
    return _store(student, uploaded.read(), 'consentimiento')


def read_consent_record(consent):
    with default_storage.open(consent.signed_file, 'rb') as stored:
        return decrypt_bytes(stored.read())


def delete_document(student, *, save=True):
    """The document and the signed authorization go together: without the document the consent is not needed."""
    if not (student.diagnosis_file or student.consent_file):
        return
    if student.pk:
        close_consents(student, paths=[student.consent_file])
    for path in (student.diagnosis_file, student.consent_file):
        if path:
            default_storage.delete(path)
    student.diagnosis_file = student.diagnosis_file_type = ''
    student.consent_file = student.consent_file_type = ''
    student.diagnosis_consent_at = None
    student.diagnosis_check = None
    if save:
        student.save(update_fields=DOCUMENT_FIELDS + ['updated_at'])
    logger.info('Diagnosis document and authorization deleted for %s', student.pseudonym)


def download_name(student):
    return f'respaldo-{student.pseudonym}.{EXTENSIONS.get(student.diagnosis_file_type, "bin")}'


def consent_download_name(student):
    return f'autorizacion-{student.pseudonym}.{EXTENSIONS.get(student.consent_file_type, "bin")}'
