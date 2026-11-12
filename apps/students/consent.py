"""
The printable authorization the student's legal representative signs before the diagnosis document is stored
(LOPDP: informed and verifiable consent for a minor's health data). It states which data is kept, what for, how
it is protected and the representative's rights; the teacher uploads it signed (photo or scan).

The form comes out with the student's name and grade printed, so the uploaded copy can be checked on this
server (check_signed): that it is this form, for this student, with the signature and the representative's
name written in. Software cannot prove a signature is genuine: the teacher still looks at it.
"""
import functools
import io
import logging
import re
from pathlib import Path
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from PIL import Image

from . import documents

logger = logging.getLogger('maestro_sombra')

BLANK = '______________________________'
SHORT_BLANK = '________________'  # inside a justified line: a long blank would stretch the words around it
# Arial where the server has it (Windows, or Linux with the Microsoft core fonts); otherwise Helvetica, which
# has the same metrics and looks almost the same
ARIAL_FOLDERS = [Path('C:/Windows/Fonts'), Path('/usr/share/fonts/truetype/msttcorefonts')]

# The signature table: the check below finds these labels in the photo and looks for handwriting beside them
FONT_SIZE = 12
SIGNATURE_LABEL = 'Firma del representante legal:'
NAME_LABEL = 'Nombres y apellidos:'
LABEL_WIDTH, VALUE_WIDTH = 6.5 * cm, 9.5 * cm
SIGNATURE_ROW, ROW = 1.5 * cm, 1.05 * cm
CELL_PADDING = 6  # reportlab's default cell padding, in points


@functools.lru_cache(maxsize=1)
def body_fonts():
    """(regular, bold) font names for the form."""
    for folder in ARIAL_FOLDERS:
        regular, bold = folder / 'arial.ttf', folder / 'arialbd.ttf'
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont('Arial', str(regular)))
            pdfmetrics.registerFont(TTFont('Arial-Bold', str(bold)))
            addMapping('Arial', 0, 0, 'Arial')
            addMapping('Arial', 1, 0, 'Arial-Bold')
            return 'Arial', 'Arial-Bold'
    return 'Helvetica', 'Helvetica-Bold'


def consent_form_pdf(*, teacher_name, student_name, grade_label):
    """The authorization as PDF bytes (Arial 12, justified), with the student's name and grade printed."""
    regular, bold = body_fonts()
    title = ParagraphStyle('t', fontName=bold, fontSize=14, leading=18, alignment=TA_CENTER, spaceAfter=4)
    subtitle = ParagraphStyle('s', fontName=regular, fontSize=10, leading=13, alignment=TA_CENTER,
                              textColor=colors.HexColor('#3d4947'), spaceAfter=12)
    body = ParagraphStyle('b', fontName=regular, fontSize=12, leading=17, alignment=TA_JUSTIFY, spaceAfter=8)
    item = ParagraphStyle('i', parent=body, leftIndent=16, bulletIndent=4, spaceAfter=4)
    small = ParagraphStyle('m', parent=body, fontSize=9, leading=12, textColor=colors.HexColor('#3d4947'))

    name = escape(student_name)
    grade = escape(grade_label) or '____________'
    teacher = escape(teacher_name or '') or BLANK
    story = [
        Paragraph('AUTORIZACIÓN DEL REPRESENTANTE LEGAL', title),
        Paragraph('Tratamiento de datos personales y del documento de diagnóstico de un estudiante con necesidades '
                  'educativas específicas · Maestro Sombra', subtitle),
        Paragraph(f'Yo, {BLANK}, en calidad de {SHORT_BLANK} (madre, padre o representante legal) del estudiante '
                  f'<b>{name}</b>, de <b>{grade}</b>, autorizo al docente sombra <b>{teacher}</b> a registrar en el '
                  'aplicativo Maestro Sombra los siguientes datos del estudiante:', body),
        Paragraph('nombre, fecha de nacimiento, grado y necesidades educativas específicas;', item, bulletText='•'),
        Paragraph('el documento que respalda el diagnóstico (informe de la UDAI, certificado del MSP, informe del DECE '
                  'o diagnóstico clínico) y su fecha;', item, bulletText='•'),
        Paragraph('las observaciones pedagógicas y las evaluaciones de las clases adaptadas.', item, bulletText='•'),
        Paragraph('<b>Finalidad.</b> Estos datos se usan únicamente para adaptar los materiales y las clases a las '
                  'necesidades del estudiante y para dar seguimiento a su aprendizaje.', body),
        Paragraph('<b>Protección.</b> El nombre, las observaciones, el documento de diagnóstico y esta autorización se '
                  'guardan cifrados. Solo el docente del estudiante puede verlos y cada apertura del documento queda '
                  'registrada. Para adaptar los materiales, la inteligencia artificial recibe solo el grado y la '
                  'necesidad educativa, con un seudónimo: nunca el nombre del estudiante ni este documento.', body),
        Paragraph('<b>Derechos.</b> Conforme a la Ley Orgánica de Protección de Datos Personales, puedo pedir en '
                  'cualquier momento acceder a estos datos, corregirlos o eliminarlos, y retirar esta autorización. '
                  'Si el estudiante deja de ser atendido, el documento de diagnóstico y esta autorización se eliminan.',
                  body),
        Spacer(1, 18),
    ]
    signatures = Table([
        [SIGNATURE_LABEL, BLANK],
        [NAME_LABEL, BLANK],
        ['Parentesco con el estudiante:', BLANK],
        ['Fecha:', '______ / ______ / __________'],
    ], colWidths=[LABEL_WIDTH, VALUE_WIDTH], rowHeights=[SIGNATURE_ROW] + [ROW] * 3)
    signatures.setStyle(TableStyle([('FONTNAME', (0, 0), (0, -1), bold), ('FONTNAME', (1, 0), (1, -1), regular),
                                    ('FONTSIZE', (0, 0), (-1, -1), FONT_SIZE), ('VALIGN', (0, 0), (-1, -1), 'BOTTOM')]))
    story += [
        signatures,
        Spacer(1, 22),
        Paragraph('Una vez firmada, el docente sube una foto o un escaneo de esta hoja en la ficha del estudiante. '
                  f'Formato generado por Maestro Sombra el {timezone.localdate():%d/%m/%Y}.', small),
    ]
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2.5 * cm, rightMargin=2.5 * cm, topMargin=2.2 * cm,
                      bottomMargin=2 * cm, title='Autorización del representante legal').build(story)
    return buffer.getvalue()


# ---------- Checking the uploaded copy ----------

INK_MIN = 0.006          # share of dark pixels beside a label (the printed line apart) that counts as handwriting
LINE_ROW_SHARE = 0.2     # a pixel row this dark across the box is the printed blank line, not handwriting
MAX_TILT = 6             # degrees a photo may be tilted


def brightness_at(image, share):
    """The grey level below which `share` of the pixels are (0.9: the paper's brightness)."""
    histogram = image.histogram()
    total, count = sum(histogram), 0
    for value, pixels in enumerate(histogram):
        count += pixels
        if count >= total * share:
            return value
    return 255


def dark_pixels(image):
    paper = brightness_at(image, 0.9)
    return image.point(lambda value: 255 if value < paper * 0.6 else 0)


def straightened(image):
    """The photo rotated so the printed lines are horizontal (the angle where the text rows are sharpest)."""
    small = image.copy()
    small.thumbnail((700, 700))
    dark = dark_pixels(small)

    def sharpness(angle):
        rows = dark.rotate(angle, fillcolor=0).resize((1, dark.height), Image.BOX).getdata()
        return sum(value * value for value in rows)

    angle = max((step / 4 for step in range(-4 * MAX_TILT, 4 * MAX_TILT + 1)), key=sharpness)
    return image.rotate(angle, resample=Image.BICUBIC, fillcolor=brightness_at(image, 0.9)) if angle else image


def _token(text):
    return re.sub(r'[^a-z0-9]', '', documents.plain(text))


def find_label(words, label):
    """(left, top, right, bottom, scale in pixels per point) of a printed label among the OCR words, or None."""
    tokens = [_token(word) for word in label.split()]
    bold = body_fonts()[1]
    for start in range(len(words)):
        run = words[start:start + len(tokens)]
        if len(run) < len(tokens) or len({word.line for word in run}) > 1:
            continue
        hits = sum(_token(word.text) == token for word, token in zip(run, tokens))
        # One word may be misread; the first one must be right
        if _token(run[0].text) == tokens[0] and hits >= len(tokens) - 1:
            left, right = run[0].left, run[-1].right
            scale = (right - left) / pdfmetrics.stringWidth(label, bold, FONT_SIZE)
            return left, min(word.top for word in run), right, max(word.bottom for word in run), scale
    return None


def handwriting(image, label_box, row_height):
    """Share of dark pixels in the blank beside a label, leaving out the printed line."""
    left, _, right, bottom, scale = label_box
    table_left = left - CELL_PADDING * scale
    box = (max(table_left + (LABEL_WIDTH + CELL_PADDING - 8) * scale, right + 4 * scale, 0),
           max(bottom - (row_height - 6) * scale, 0),
           min(table_left + (LABEL_WIDTH + VALUE_WIDTH) * scale, image.width),
           min(bottom + 8 * scale, image.height))
    box = tuple(round(value) for value in box)
    if box[2] - box[0] < 20 or box[3] - box[1] < 10:
        return 0
    rows = list(dark_pixels(image.crop(box)).resize((1, box[3] - box[1]), Image.BOX).getdata())
    margin = max(2, round(2 * scale))
    printed = {near for row, value in enumerate(rows) if value > 255 * LINE_ROW_SHARE
               for near in range(row - margin, row + margin + 1)}
    return sum(value for row, value in enumerate(rows) if row not in printed) / 255 / len(rows)


def grade_found(text, grade_label):
    number = re.match(r'\d+', grade_label or '')
    return not number or re.search(rf'(?<!\d){number.group()}\D{{0,5}}de egb', text) is not None


def check_signed(content, content_type, *, student_name, grade_label):
    """
    Why the uploaded authorization cannot be accepted, or None. Checks, on this server: it is the app's form,
    it was printed for this student (name and grade) and the signature and the representative's name are written.
    """
    image = documents.first_page_image(content, content_type)
    if image is None:
        return 'No se pudo abrir la autorización. Sube una foto o un escaneo en PDF, JPG o PNG.'
    image = straightened(image)
    words = documents.ocr_words(image)
    if words is None:
        # Without OCR the copy cannot be checked here: the teacher's own look is the check
        logger.warning('Signed authorization stored without automatic check: OCR is not installed')
        return None
    text = ' '.join(documents.plain(' '.join(word.text for word in words)).split())
    if len(text) < 20:
        return ('No se pudo leer la autorización (hoja en blanco, foto borrosa u oscura). Sube una foto nítida de la '
                'hoja completa, de frente y con buena luz, o un escaneo.')
    if 'autorizacion' not in text or not ('representante legal' in text or 'maestro sombra' in text):
        return ('El archivo no es el formato de autorización de Maestro Sombra. Descárgalo con el botón de arriba, '
                'que el representante lo llene y lo firme, y sube una foto o un escaneo.')
    problems = []
    if not documents.name_found(text, student_name):
        problems.append(f'no tiene impreso el nombre de {student_name}')
    elif not grade_found(text, grade_label):
        problems.append(f'el grado impreso no es {grade_label}')
    signature, name = find_label(words, SIGNATURE_LABEL), find_label(words, NAME_LABEL)
    if not (signature or name):
        problems.append('no se distingue la parte de las firmas')
    else:
        if signature and handwriting(image, signature, SIGNATURE_ROW) < INK_MIN:
            problems.append('falta la firma del representante legal')
        if name and handwriting(image, name, ROW) < INK_MIN:
            problems.append('faltan los nombres y apellidos del representante')
    if not problems:
        return None
    return (f'La autorización no es válida: {documents.listed(problems)}. Descarga el formato desde esta ficha (sale '
            'con los datos del estudiante), que el representante lo llene y lo firme, y sube una foto nítida de la '
            'hoja completa.')
