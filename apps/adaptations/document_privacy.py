"""
Privacy check of a document BEFORE anything of it is sent to an external provider (Gemini). Local only: it never
uses the AI to look at data that has not been cleared yet.

Safe by default:
- inspected and clean            -> APTO      (sent as it was read)
- identifiers removed verifiably -> DEPURADO  (only the cleaned version is sent)
- identifiers that cannot be removed safely, or a file that cannot be inspected, or any error -> BLOQUEADO
  (nothing is sent; the teacher gets a message without the sensitive data)
The original file is never sent: only the cleaned text and, for digital PDFs, page images where every inspected
identifier is covered and every embedded image (photo, scanned stamp, signature) is blanked out.

By format:
- typed text: identifiers replaced;
- Word: text of body, tables, headers and footers, replaced; its embedded images and metadata are not sent;
- digital PDF: text replaced, and the page images redacted with the exact position of that text;
- scanned PDF, photos, and anything not inspectable: blocked (handwriting, faces or signatures in an image
  cannot be found reliably on this server).
The identifiers looked for are a minimum, not every possible personal datum: the teacher still reviews.
"""
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from django.conf import settings
from docx import Document
from PIL import ImageDraw

APTO, DEPURADO, BLOQUEADO = 'APTO', 'DEPURADO', 'BLOQUEADO'
MAX_PAGES = 10
RENDER_SCALE = 2
MAX_SIDE = 1600
MIN_PAGE_TEXT = 25         # characters: a page with less text and with images is a scan
MAX_UNREADABLE_SHARE = 0.1  # share of characters without a readable code: the text cannot be trusted

PLACEHOLDERS = {'nombre': '[nombre]', 'correo': '[correo]', 'telefono': '[teléfono]', 'identificacion': '[identificación]',
                'direccion': '[dirección]', 'nacimiento': '[fecha]', 'fecha': '[fecha]', 'firma': '[firma]',
                'codigo': '[código]'}
KIND_NAMES = {'nombre': 'nombres', 'correo': 'correos electrónicos', 'telefono': 'teléfonos',
              'identificacion': 'números de identificación', 'direccion': 'direcciones', 'nacimiento': 'fechas de nacimiento',
              'fecha': 'fechas completas', 'firma': 'firmas', 'codigo': 'códigos de estudiante',
              'metadatos': 'metadatos con datos personales', 'imagenes': 'imágenes incrustadas'}
KIND_NAME = {'nombre': 'nombre', 'correo': 'correo electrónico', 'telefono': 'teléfono',
             'identificacion': 'número de identificación', 'direccion': 'dirección', 'nacimiento': 'fecha de nacimiento',
             'fecha': 'fecha completa', 'firma': 'firma', 'codigo': 'código de estudiante',
             'metadatos': 'metadato con datos personales', 'imagenes': 'imagen incrustada'}

# A labelled value ("Nombre: Ana Ruiz", "Cédula: 0912…"): only the group "v" is replaced, the label stays.
# A name is up to five capitalized words on the same line, and stops before the next label ("Cédula:")
NAME_WORDS = r"(?:[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ'-]+(?![A-Za-zÁÉÍÓÚÑáéíóúñ'-]*\s*:)\.?[ \t]*){1,5}"
TITLED_NAME = re.compile(
    r'\b(?i:lcd[oa]s?|lic|licenciad[oa]|ing|msc|mgs|mg|mgtr|dr|dra|phd|prof|profa|psic|ps|abg|tlgo|tnlgo|econ)\b'
    r'(?:\.[ \t]*|[ \t]+)' + NAME_WORDS)
DATE_VALUE = r'\d{1,2}[ \t]*[/-][ \t]*\d{1,2}[ \t]*[/-][ \t]*\d{2,4}|\d{1,2}[ \t]+de[ \t]+[a-záéíóú]+[ \t]+del?[ \t]+\d{4}'
PATTERNS = [
    ('correo', re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')),
    ('nacimiento', re.compile(r'(?i:fecha de nacimiento)[ \t]*:?[ \t]*(?P<v>' + DATE_VALUE + ')')),
    ('direccion', re.compile(r'(?i:direcci[oó]n|domicilio)[ \t]*:[ \t]*(?P<v>[^\n|;]{3,60})')),
    ('firma', re.compile(r'(?i:firma)[ \t]*:[ \t]*(?P<v>[^\n|;]{1,40})')),
    ('codigo', re.compile(r'(?i:c[oó]digo del estudiante|c[oó]digo|matr[ií]cula)[ \t]*:[ \t]*(?P<v>[A-Za-z0-9-]{3,})'
                          r'|\bEST-[A-F0-9]{6}\b')),
    ('nombre', re.compile(r'(?i:\b(?:nombres? y apellidos|nombre del estudiante|nombre|estudiante|alumn[oa]|'
                          r'representante|tutora?|docente|profesora?))[ \t]*:[ \t]*(?P<v>' + NAME_WORDS + ')')),
    ('nombre', TITLED_NAME),
    # Ecuadorian cédula (10 digits), RUC (13), "123456789-0", and labelled document numbers
    ('identificacion', re.compile(r'(?i:c[eé]dula|c\.i\.|pasaporte|identificaci[oó]n)[ \t]*:?[ \t]*(?P<v>[\dA-Z-]{6,})'
                                  r'|\b\d{9}-\d\b|\b\d{10}(?:001)?\b')),
    ('telefono', re.compile(r'(?:\+593[\s-]?|\b0)9\d(?:[\s-]?\d){7}\b|\b0[2-7][\s-]?\d{3}[\s-]?\d{4}\b')),
    ('fecha', re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b')),
]


@dataclass
class Inspection:
    status: str
    text: str = ''
    images: list = field(default_factory=list)
    findings: Counter = field(default_factory=Counter)
    reasons: list = field(default_factory=list)

    @property
    def allowed(self):
        return self.status in (APTO, DEPURADO)

    @property
    def message(self):
        """For the teacher: what was done or why it was blocked, never the data itself."""
        if self.status == BLOQUEADO:
            return ' '.join(self.reasons)
        if self.status == DEPURADO:
            found = ', '.join(f'{count} {(KIND_NAME if count == 1 else KIND_NAMES)[kind]}'
                              for kind, count in self.findings.items())
            return f'Antes de enviar el material a la IA se quitaron: {found}.'
        return ''


def blocked(*reasons):
    return Inspection(status=BLOQUEADO, reasons=list(reasons))


# ---------- Text ----------

def _spans(text, names=()):
    """(start, end, kind) of every identifier in the text, without overlaps."""
    spans = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            group = 'v' if 'v' in pattern.groupindex and m.group('v') else 0
            start, end = m.start(group), m.end(group)
            while end > start and text[end - 1] in ' \t':
                end -= 1
            if text[start:end].strip():
                spans.append((start, end, kind))
    words = sorted({word for name in names for word in str(name).split() if len(word) > 2}, key=len, reverse=True)
    for word in words:
        spans += [(m.start(), m.end(), 'nombre')
                  for m in re.finditer(rf'(?<![\w]){re.escape(word)}(?![\w])', text, re.IGNORECASE)]
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    clean, last_end = [], -1
    for start, end, kind in spans:
        if start >= last_end:
            # Keep a leading space or colon out of the replaced part
            while start < end and text[start] in ' :#\t':
                start += 1
            if start < end:
                clean.append((start, end, kind))
                last_end = end
    return clean


def sanitize_text(text, names=()):
    """(clean text, Counter of what was replaced). Never logs or returns the replaced values."""
    text = str(text or '')
    found = Counter()
    pieces, cursor = [], 0
    for start, end, kind in _spans(text, names):
        pieces += [text[cursor:start], PLACEHOLDERS[kind]]
        found[kind] += 1
        cursor = end
    pieces.append(text[cursor:])
    return ''.join(pieces), found


def inspect_text(text, names=()):
    clean, found = sanitize_text(text, names)
    if not clean.strip():
        return blocked('El texto está vacío: no hay nada que adaptar.')
    return Inspection(status=DEPURADO if found else APTO, text=clean, findings=found)


# ---------- Files ----------

def file_kind(raw):
    """The real type from the first bytes (the extension or the browser's word is not trusted)."""
    if raw[:5] == b'%PDF-':
        return 'pdf'
    if raw[:8] == b'\x89PNG\r\n\x1a\n' or raw[:3] == b'\xff\xd8\xff' or (raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'):
        return 'image'
    if raw[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if 'word/document.xml' in archive.namelist():
                    return 'docx'
        except zipfile.BadZipFile:
            return None
    return None


def inspect_file(raw, names=()):
    """Inspection of an uploaded file. Any unexpected error blocks it: the original is never sent by mistake."""
    try:
        if not raw:
            return blocked('El archivo está vacío.')
        if len(raw) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            return blocked(f'El archivo supera los {settings.MAX_UPLOAD_MB} MB.')
        kind = file_kind(raw)
        if kind == 'image':
            return blocked('Por privacidad, las fotos no se envían a la IA: en una imagen no se puede comprobar de '
                           'forma confiable que no haya nombres, firmas, rostros u otros datos personales. Sube el '
                           'material como PDF digital o Word, o escribe el texto.')
        if kind == 'docx':
            return _inspect_docx(raw, names)
        if kind == 'pdf':
            return _inspect_pdf(raw, names)
        return blocked('El tipo de archivo no se reconoce: sube un PDF digital, un Word (.docx) o escribe el texto.')
    except Exception:  # noqa: BLE001 - safe by default: an error never lets the original through
        return blocked('No se pudo revisar el archivo, así que no se envió a la IA. Sube otra versión (PDF digital '
                       'o Word) o escribe el texto.')


def _person_metadata(values):
    return any(str(value or '').strip() for value in values)


def _inspect_docx(raw, names):
    document = Document(io.BytesIO(raw))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(' | '.join(cell.text.strip() for cell in row.cells))
    for section in document.sections:
        for part in (section.header, section.footer, section.first_page_header, section.first_page_footer):
            lines += [p.text for p in part.paragraphs]
            for table in part.tables:
                for row in table.rows:
                    lines.append(' | '.join(cell.text.strip() for cell in row.cells))
    text = '\n'.join(line for line in lines if line.strip())
    clean, found = sanitize_text(text, names)
    if not clean.strip():
        return blocked('El Word no tiene texto que se pueda revisar (quizás solo tiene imágenes). Escribe el texto '
                       'o súbelo como PDF digital.')
    props = document.core_properties
    if _person_metadata([props.author, props.last_modified_by, props.comments]):
        found['metadatos'] += 1  # only the text is sent: the metadata never leaves
    images = sum(1 for part in document.part.related_parts.values() if part.content_type.startswith('image/'))
    if images:
        found['imagenes'] += images  # not sent: their content cannot be inspected
    return Inspection(status=DEPURADO if found else APTO, text=clean, findings=found)


def _page_findings(textpage, names):
    """(page text, spans of identifiers) using the PDF's own characters, so their position is exact."""
    count = textpage.count_chars()
    text = textpage.get_text_range(0, count) if count else ''
    return text, _spans(text, names)


def _redacted_page(page, textpage, spans):
    """The page rendered with every identifier covered and every embedded image blanked out."""
    width, height = page.get_size()
    image = page.render(scale=RENDER_SCALE).to_pil().convert('RGB')
    draw = ImageDraw.Draw(image)
    blanked = 0
    for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,)):
        left, bottom, right, top = obj.get_pos()
        box = [left * RENDER_SCALE, (height - top) * RENDER_SCALE, right * RENDER_SCALE, (height - bottom) * RENDER_SCALE]
        draw.rectangle(box, fill='white', outline=(170, 170, 170), width=2)
        draw.text((box[0] + 8, box[1] + 8), 'imagen omitida por privacidad', fill=(120, 120, 120))
        blanked += 1
    for start, end, _kind in spans:
        boxes = [textpage.get_charbox(i) for i in range(start, end)]
        boxes = [b for b in boxes if b[2] > b[0] or b[3] > b[1]]
        if not boxes:
            continue
        left, bottom = min(b[0] for b in boxes), min(b[1] for b in boxes)
        right, top = max(b[2] for b in boxes), max(b[3] for b in boxes)
        draw.rectangle([left * RENDER_SCALE - 3, (height - top) * RENDER_SCALE - 3,
                        right * RENDER_SCALE + 3, (height - bottom) * RENDER_SCALE + 3], fill='black')
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image, blanked


def _unreadable(text):
    """Text that PDFium cannot decode (fonts without a character map): what it shows cannot be inspected."""
    visible = [c for c in text if not c.isspace()]
    bad = sum(1 for c in visible if c == '�' or (ord(c) < 32) or 0xE000 <= ord(c) <= 0xF8FF)
    return bool(visible) and bad / len(visible) > MAX_UNREADABLE_SHARE


def _inspect_pdf(raw, names):
    pdf = pdfium.PdfDocument(raw)
    try:
        found = Counter()
        texts, images = [], []
        for index in range(min(len(pdf), MAX_PAGES)):
            page = pdf[index]
            textpage = page.get_textpage()
            text, spans = _page_findings(textpage, names)
            has_images = any(True for _ in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,)))
            if len(text.strip()) < MIN_PAGE_TEXT:
                if has_images:
                    return blocked('El PDF es escaneado (sus páginas son imágenes): no se puede comprobar que no '
                                   'tenga datos personales. Sube un PDF digital, un Word o escribe el texto.')
                continue  # a blank or drawing-only page: nothing personal can be read in it
            if _unreadable(text):
                return blocked('El texto del PDF no se puede leer con seguridad (fuentes sin codificación). Sube '
                               'otra versión del PDF, un Word o escribe el texto.')
            for _start, _end, kind in spans:
                found[kind] += 1
            image, blanked = _redacted_page(page, textpage, spans)
            if blanked:
                found['imagenes'] += blanked
            images.append(image)
            texts.append(sanitize_text(text, names)[0])
        if not texts:
            return blocked('El PDF no tiene texto que se pueda revisar. Sube un PDF digital, un Word o escribe el texto.')
        metadata = pdf.get_metadata_dict()
        if _person_metadata([metadata.get('Author'), metadata.get('Creator') if '@' in str(metadata.get('Creator')) else '']):
            found['metadatos'] += 1  # never sent: only the rendered, redacted pages and the cleaned text leave
        return Inspection(status=DEPURADO if found else APTO, text='\n'.join(texts), images=images, findings=found)
    finally:
        pdf.close()


def inspect_material(material, names=()):
    """The inspection of an uploaded material (Adaptar material or the book pages of Adaptar clase)."""
    if material.input_type == 'TEXT':
        return inspect_text(material.input_text, names)
    try:
        with material.file.open('rb') as stored:
            raw = stored.read()
    except Exception:  # noqa: BLE001
        return blocked('No se pudo abrir el archivo del material, así que no se envió a la IA.')
    return inspect_file(raw, names)
