"""
The teacher's class plan for "Adaptar clase": Excel (.xlsx or the old .xls), Word (.docx) or a digital PDF.

Every school has its own format, so nothing here depends on fixed cells: each file becomes the same
rows of text (a sheet per Excel sheet; one "Documento" for Word and PDF), the sessions are found by
their label ("Sesión 30", "Reto 15", "Clase 4") and each session becomes plain "label: value" text
that the AI interprets. Before that text leaves the server, the rows that identify the school,
the teachers or the student (institution, motto, names, signatures, dates, the current
"Adaptación curricular" row with the student's initials) are dropped. The result is returned as a
new Excel with only that session, adapted: the teacher's original file is not copied.
Scanned PDFs are not accepted: their text would have to be guessed, and so would the names to drop.
"""
import io
import re
import unicodedata
import zipfile
from datetime import date, datetime

import openpyxl
import openpyxl.utils
import pypdfium2
import xlrd
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

SESSION_LABEL = re.compile(r'^\s*(sesi[oó]n|reto|clase)\s*n?[º°.]?\s*\d+', re.IGNORECASE)
# In Word paragraphs and PDF lines the label may follow another one: "Nº de sesión: Sesión 33"
SESSION_IN_LINE = re.compile(r'(?:^|[:|·]\s*)((?:sesi[oó]n|reto)\s*n?[º°.]?\s*\d+)', re.IGNORECASE)
TITLE_IN_LINE = re.compile(r't[ií]tulo[^:]*:\s*(.+)$', re.IGNORECASE)
# A week heading in Word and PDF ("SEMANA 19", "Planificación · Semana 19"): short, so not a sentence
WEEK_LABEL = re.compile(r'\bsemana\s*n?[º°.]?\s*(\d{1,2})\b', re.IGNORECASE)
MAX_WEEK_HEADING = 60
# Rows with these words identify people or the school, or are administrative: never sent to the AI
PRIVATE_WORDS = ('docente', 'nombre', 'firma', 'revisado', 'aprobado', 'realizado', 'elaborado', 'unidad educativa',
                 'escuela', 'colegio', 'institucion', 'formato', 'actualiz', 'pagina', 'paralelo', 'fecha', 'periodo',
                 'ano lectivo', 'jefe de area', 'coordinacion', 'rector', 'vicerrector', 'observaciones',
                 'bibliografia', 'adaptacion curricular', 'estudiante', 'alumno:')
# A person written with their title ("Lcda. Ximena Alvarado", "Ing. Silvia Moreira", "MSc. …"): in Word and
# PDF the name often lands on its own line, away from the "Docente:" or "Elaborado por:" label that marks it
TITLE_AND_NAME = re.compile(
    # The title in any case (LCDA., Lcda.); the name, words starting with a capital letter
    r'\b(?i:lcd[oa]s?|lic|licenciad[oa]|ing|msc|mgs|mg|mgtr|dr|dra|phd|prof|profa|psic|ps|abg|tlgo|tnlgo|econ)\b'
    r'(?:\.\s*|\s+)(?:[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+\.?\s*){1,4}')
# A plan's pedagogical part starts at its first row with one of these labels (before it: banner, motto)
PEDAGOGICAL_WORDS = ('destreza', 'indicador', 'objetivo', 'eje', 'sesion', 'reto', 'tema', 'calentemos',
                     'inicio', 'anticipacion', 'desarrollo', 'contenido')
# The fields every week repeats before its sessions (in Word and PDF, where weeks are not separate sheets)
HEADER_WORDS = ('destreza', 'indicador')
# Where a week's sessions are over in Word and PDF: the next week's heading or the signature block
WEEK_END_WORDS = ('unidad educativa', 'revisado por', 'elaborado por', 'firma')
URL = re.compile(r'https?://\S+')
# Dates written as text (Word, PDF): they identify the school year and the class day
DATE = re.compile(r'\b\d{1,2}\s*[/-]\s*\d{1,2}\s*[/-]\s*\d{2,4}\b|\b\d{1,2}\s+de\s+[a-záéíóú]+\s+del?\s+\d{4}\b'
                  r'|\b(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\b', re.IGNORECASE)
MAX_TEXT = 7000
MAX_WEEK_TEXT = 16000  # a whole week: two or three sessions
MIN_PDF_TEXT = 80  # characters: less means a scanned PDF (only images)

XLSX, XLS, DOCX, PDF = 'xlsx', 'xls', 'docx', 'pdf'
EXTENSIONS = {XLSX: '.xlsx', XLS: '.xls', DOCX: '.docx', PDF: '.pdf'}
TEXT_DOCUMENT = 'Documento'


class PlanError(ValueError):
    """The file cannot be read as a class plan. The message is shown to the teacher."""


def plain(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower().strip()


def plan_format(content):
    """xlsx, xls, docx or pdf, from the file's own bytes (the extension is not trusted)."""
    if content.startswith(b'%PDF-'):
        return PDF
    if content.startswith(b'\xd0\xcf\x11\xe0'):
        return XLS
    if content.startswith(b'PK'):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(content)).namelist())
        except zipfile.BadZipFile:
            return None
        if 'word/document.xml' in names:
            return DOCX
        if 'xl/workbook.xml' in names:
            return XLSX
    return None


def _cell_text(value):
    if value is None or isinstance(value, (datetime, date)):
        return ''  # dates identify the school year and the class day: not needed to adapt
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return ' '.join(str(value).split())


def _texts(values):
    texts = [_cell_text(value) for value in values]
    return [text for text in texts if text]


def _xlsx_sheets(content):
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    for sheet in workbook.worksheets:
        rows = [(row[0].row, _texts(cell.value for cell in row)) for row in sheet.iter_rows()]
        yield sheet.title, [(number, texts) for number, texts in rows if texts]


def _xls_sheets(content):
    book = xlrd.open_workbook(file_contents=content)
    for sheet in book.sheets():
        rows = []
        for index in range(sheet.nrows):
            values = [None if cell.ctype == xlrd.XL_CELL_DATE else cell.value for cell in sheet.row(index)]
            texts = _texts(values)
            if texts:
                rows.append((index + 1, texts))
        yield sheet.name, rows


def _docx_sheets(content):
    """Paragraphs and table rows in the order they appear; merged cells repeat their text once."""
    document = Document(io.BytesIO(content))
    rows = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith('}p'):
            texts = _texts([Paragraph(child, document).text])
            if texts:
                rows.append(texts)
        elif child.tag.endswith('}tbl'):
            for row in Table(child, document).rows:
                texts = []
                for text in _texts(cell.text for cell in row.cells):
                    if not texts or texts[-1] != text:
                        texts.append(text)
                if texts:
                    rows.append(texts)
    yield TEXT_DOCUMENT, list(enumerate(rows, start=1))


def _pdf_sheets(content):
    pdf = pypdfium2.PdfDocument(content)
    try:
        text = '\n'.join(pdf[i].get_textpage().get_text_bounded() for i in range(len(pdf)))
    finally:
        pdf.close()
    if len(text.strip()) < MIN_PDF_TEXT:
        raise PlanError('Este PDF es escaneado (solo imágenes): no se puede leer su texto. Sube la planificación '
                        'en Excel, en Word o en un PDF hecho desde la computadora.')
    rows = [_texts([line]) for line in text.splitlines()]
    yield TEXT_DOCUMENT, list(enumerate([texts for texts in rows if texts], start=1))


READERS = {XLSX: _xlsx_sheets, XLS: _xls_sheets, DOCX: _docx_sheets, PDF: _pdf_sheets}


def read_plan(content):
    """[(sheet name, [(row number, [cell texts])])] for any accepted format."""
    kind = plan_format(content)
    if kind is None:
        raise PlanError('Sube la planificación en Excel (.xlsx o .xls), Word (.docx) o PDF.')
    try:
        return list(READERS[kind](content))
    except PlanError:
        raise
    except Exception as error:  # noqa: BLE001 - a damaged file of a known type
        raise PlanError('No se pudo leer el archivo. Verifica que no esté dañado ni protegido con contraseña.') from error


def _session_label(texts):
    """'Sesión 30' if this row starts a session: a cell with the label, or a line where it follows a colon."""
    label = next((text for text in texts if SESSION_LABEL.match(text)), None)
    if label:
        return label
    for text in texts:
        found = SESSION_IN_LINE.search(text)
        if found:
            return found.group(1)
    return None


def _session_title(texts):
    # The title is usually the value after a "Título…" label: the next cell, or after the colon in a line
    for index, text in enumerate(texts):
        if plain(text).startswith('titulo') and index + 1 < len(texts):
            return texts[index + 1].rstrip(' .')
        found = TITLE_IN_LINE.search(text)
        if found:
            return found.group(1).strip().rstrip(' .')
    return ''


def _week_heading(texts):
    """'Semana 19' if this row is a week heading of a Word or PDF plan."""
    joined = ' '.join(texts)
    found = WEEK_LABEL.search(joined) if len(joined) <= MAX_WEEK_HEADING else None
    return f'Semana {found.group(1)}' if found else None


def sessions(content):
    """
    Every session found in the plan: [{'key', 'sheet', 'row', 'label', 'title', 'week'}]. The week is the
    Excel sheet (one per week) or, in Word and PDF, the last "Semana N" heading before the session.
    """
    found = []
    for sheet, rows in read_plan(content):
        sheet_sessions = []
        week = (_week_heading([sheet]) or sheet.strip()) if sheet != TEXT_DOCUMENT else 'Documento'
        for number, texts in rows:
            if sheet == TEXT_DOCUMENT:
                week = _week_heading(texts) or week
            label = _session_label(texts)
            if label:
                sheet_sessions.append({'key': f'{sheet}|{number}', 'sheet': sheet, 'row': number,
                                       'label': label, 'title': _session_title(texts), 'week': week})
        if not sheet_sessions and len(rows) > 5:
            # A plan without numbered sessions ("Adaptación", "Refuerzo"…): the whole sheet (or document) is
            # one class, named after the titles its sessions do have
            titles = list(dict.fromkeys(title for _number, texts in rows if (title := _session_title(texts))))
            sheet_sessions.append({'key': f'{sheet}|0', 'sheet': sheet, 'row': 0,
                                   'label': 'Hoja completa' if sheet != TEXT_DOCUMENT else 'Documento completo',
                                   'title': ' y '.join(', '.join(titles[:3]).rsplit(', ', 1)), 'week': week})
        found += sheet_sessions
    return found


def weeks(found_sessions):
    """
    The classes the teacher chooses from: one per week (a week has one topic; its sessions are the class
    days), with all its sessions, from the first one's row to the last one's. In Excel a week is its sheet
    (row 0, the whole sheet); in Word and PDF, the sessions under the same "Semana N" heading. The title is
    the topic: the titles of its sessions ("Practicamos y Relacionamos multiplicaciones").
    """
    groups = {}
    for session in found_sessions:
        groups.setdefault((session['sheet'], session['week']), []).append(session)
    result = []
    for (sheet, week), items in groups.items():
        numbered = [item for item in items if item['row']]
        whole_sheet = sheet != TEXT_DOCUMENT or not numbered
        titles = list(dict.fromkeys(item['title'] or item['label'] for item in numbered))
        topic = (' y '.join(', '.join(titles).rsplit(', ', 1)) if titles
                 else next((item['title'] for item in items if item['title']), 'Semana completa'))
        result.append({
            'key': f'{sheet}|semana|{numbered[0]["row"] if numbered else 0}', 'sheet': sheet, 'week': week,
            'is_week': True, 'row': 0 if whole_sheet else numbered[0]['row'],
            'last_row': None if whole_sheet else numbered[-1]['row'],
            'label': week, 'title': topic, 'sessions': [item['label'] for item in numbered],
        })
    return result


def _is_private(texts):
    joined = plain(' '.join(texts))
    first = plain(texts[0])
    return any(first.startswith(word) or f' {word}' in f' {first}' for word in PRIVATE_WORDS) or (
        len(texts) == 1 and any(word in joined for word in ('unidad educativa', 'formando', 'lema')))


def _clean(text):
    return ' '.join(TITLE_AND_NAME.sub('', DATE.sub('', URL.sub('', text))).split())


def session_text(content, sheet_name, row, last_row=None):
    """
    The chosen session as 'label: value' lines, without personal or school data. With last_row (a whole
    week in Word or PDF) it runs from the session at row to the end of the session at last_row.
    """
    rows = dict(read_plan(content))[sheet_name]
    limit = MAX_WEEK_TEXT if (last_row or not row) else MAX_TEXT
    starts = [number for number, texts in rows if _session_label(texts)]
    pedagogical = next((number for number, texts in rows
                        if any(word in plain(' '.join(texts)) for word in PEDAGOGICAL_WORDS)), 0)
    first_session = starts[0] if starts else None
    header = (pedagogical, first_session or 10 ** 9)  # an Excel sheet is one week: its fields come first
    if sheet_name == TEXT_DOCUMENT and row:
        # A Word or PDF document holds every week one after another: the session's week fields (skill,
        # indicator…) are the last "Destreza / Indicador" block before it, up to that week's first session
        before = [(number, plain(' '.join(texts))) for number, texts in rows if number < row]
        # The block starts at its "Destreza" line (the indicator usually comes right after it)
        header_start = next((number for word in HEADER_WORDS for number, text in reversed(before) if word in text),
                            None)
        if header_start is not None:
            header = (header_start, next((start for start in starts if start > header_start), row))
        pedagogical = header[0]
    last = last_row or row
    end = next((start for start in starts if start > last), None) if row else None
    if sheet_name == TEXT_DOCUMENT and row:
        # ...and a session also ends where the next week's heading or the signatures start
        heading = next((number for number, texts in rows if number > last and
                        any(word in plain(' '.join(texts)) for word in WEEK_END_WORDS)), None)
        end = min(n for n in (end, heading) if n is not None) if (end or heading) else None
    lines = []
    for number, texts in rows:
        shared = header[0] <= number < header[1]  # week-level fields: skill, indicator…
        in_session = (not row) or (row <= number and (end is None or number < end))
        if not (shared or in_session) or number < pedagogical or _is_private(texts):
            continue
        lines.append(' | '.join(_clean(text) for text in texts if _clean(text)))
    return '\n'.join(line for line in lines if line)[:limit]


# ---------- The adapted class plan as a new, clean Excel ----------

TEAL, SOFT, BORDER = '00685F', 'E6F4F2', 'C9D4D2'
COLUMNS = [('Momento', 16), ('Duración', 11), ('Lo que hace la clase', 34), ('Qué hace el docente sombra', 38),
           ('Qué hace el estudiante', 32), ('Apoyos', 30), ('Qué vigilar', 28)]


SHEET_NAME_FORBIDDEN = re.compile(r'[\[\]:*?/\\]')


def unique_sheet_name(name, used):
    """A valid Excel sheet name (31 characters, no []:*?/\\), different from the ones already used."""
    base = SHEET_NAME_FORBIDDEN.sub(' ', name).strip()[:31] or 'Estudiante'
    candidate, number = base, 2
    while candidate.lower() in used:
        suffix = f' ({number})'
        candidate, number = base[:31 - len(suffix)] + suffix, number + 1
    used.add(candidate.lower())
    return candidate


def adapted_workbook(sheets):
    """
    Only the chosen session, adapted: one independent sheet per student, [(sheet name, data)], where data
    holds the keyword arguments of write_class_sheet. Built from scratch so nothing of the original file
    leaks: inside the sheet the student appears by pseudonym, with no institution or teacher names.
    """
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    used = set()
    for name, data in sheets:
        write_class_sheet(workbook.create_sheet(unique_sheet_name(name, used)), **data)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def class_sections(*, analysis, content, subject, grade, pseudonym, condition, adaptation_grade, plan_row):
    """
    The class adapted for one student, in five sections, shared by the Excel, PDF and Word downloads:
    [('fields', title, [(label, value, bold)]) or ('moments', title, [[the 7 columns]])].
    """
    originals = {plain(m.get('name')): m.get('activities', '') for m in analysis.get('moments', [])}
    moments = [[moment.get('name', ''), moment.get('duration', ''), originals.get(plain(moment.get('name')), ''),
                '\n'.join(f'• {action}' for action in moment.get('teacher_actions', [])),
                moment.get('student_task', ''), '\n'.join(f'• {support}' for support in moment.get('supports', [])),
                moment.get('watch_for', '')]
               for moment in content.get('moments', [])]
    return [
        ('fields', '1. Datos de la clase y del estudiante', [
            ('Materia', subject, False), ('Clase', analysis.get('title', ''), False), ('Grado', grade, False),
            ('Estudiante', f'{pseudonym} (seudónimo) · {condition}', False),
            ('Grado de adaptación', adaptation_grade, True), ('Justificación', content.get('grade_reason', ''), False),
        ]),
        ('fields', '2. Currículo', [
            ('Destreza con criterio de desempeño', analysis.get('skill', ''), False),
            ('Indicador de evaluación', analysis.get('indicator', ''), False),
            ('Objetivo de la clase', analysis.get('objective', ''), False),
            ('Objetivo adaptado', content.get('adapted_objective', ''), True),
        ]),
        ('moments', '3. Momentos de la clase adaptados', moments),
        ('fields', '4. Materiales, evaluación y cierre', [
            ('Materiales de la clase', analysis.get('materials', ''), False),
            ('Evaluación adaptada', content.get('evaluation', ''), False),
            ('Preguntas de cierre', '\n'.join(f'• {q}' for q in content.get('metacognition', [])), False),
        ]),
        ('fields', '5. Adaptación curricular (para la planificación)', [
            ('Adaptación curricular', plan_row, True),
        ]),
    ]


TITLE = 'PLAN DE CLASE ADAPTADO'
NOTE = 'Generado con Maestro Sombra. Revise y ajuste la adaptación antes de entregarla.'


def write_class_sheet(sheet, **data):
    """The class adapted for one student, in its five sections, on an Excel sheet."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    side = Side(style='thin', color=BORDER)
    border = Border(left=side, right=side, top=side, bottom=side)
    wrap = Alignment(wrap_text=True, vertical='top')
    for index, (_, width) in enumerate(COLUMNS, start=1):
        sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = width
    last = openpyxl.utils.get_column_letter(len(COLUMNS))
    row = 1
    sheet.cell(row, 1, TITLE).font = Font(bold=True, size=16, color=TEAL)
    sheet.merge_cells(f'A{row}:{last}{row}')
    row += 2
    for kind, title, rows in class_sections(**data):
        cell = sheet.cell(row, 1, title)
        cell.font = Font(bold=True, size=12, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor=TEAL)
        sheet.merge_cells(f'A{row}:{last}{row}')
        row += 1
        if kind == 'moments':
            for column, (header, _) in enumerate(COLUMNS, start=1):
                cell = sheet.cell(row, column, header)
                cell.font = Font(bold=True, color=TEAL)
                cell.fill = PatternFill('solid', fgColor=SOFT)
                cell.alignment = wrap
                cell.border = border
            row += 1
            for values in rows:
                for column, value in enumerate(values, start=1):
                    cell = sheet.cell(row, column, value)
                    cell.alignment = wrap
                    cell.border = border
                sheet.row_dimensions[row].height = max(60, 15 * (max(len(str(v)) for v in values) // 30 + 1))
                row += 1
        else:
            for label, value, bold in rows:
                sheet.cell(row, 1, label).font = Font(bold=True, color=TEAL)
                sheet.cell(row, 1).fill = PatternFill('solid', fgColor=SOFT)
                sheet.cell(row, 1).alignment = wrap
                sheet.cell(row, 2, value).alignment = wrap
                sheet.cell(row, 2).font = Font(bold=bold)
                sheet.merge_cells(f'B{row}:{last}{row}')
                for column in range(1, len(COLUMNS) + 1):
                    sheet.cell(row, column).border = border
                # Rough height so long texts stay readable without opening each cell
                sheet.row_dimensions[row].height = max(18, 15 * (len(str(value)) // 150 + 1))
                row += 1
        row += 1
    note = sheet.cell(row, 1, NOTE)
    note.font = Font(italic=True, color='5B6765')
    sheet.merge_cells(f'A{row}:{last}{row}')
    sheet.sheet_view.showGridLines = False
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def adapted_pdf(classes):
    """The adapted class as a landscape PDF, [(student name, data)]: one student after another."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    teal, soft, line = colors.HexColor(f'#{TEAL}'), colors.HexColor(f'#{SOFT}'), colors.HexColor(f'#{BORDER}')
    text = ParagraphStyle('t', fontName='Helvetica', fontSize=8.5, leading=11)
    bold = ParagraphStyle('b', parent=text, fontName='Helvetica-Bold')
    label = ParagraphStyle('l', parent=bold, textColor=teal)
    header = ParagraphStyle('h', fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=colors.white)
    big = ParagraphStyle('g', fontName='Helvetica-Bold', fontSize=16, leading=20, textColor=teal, spaceAfter=8)
    note = ParagraphStyle('n', parent=text, fontName='Helvetica-Oblique', textColor=colors.HexColor('#5b6765'))
    width = landscape(A4)[0] - 3 * cm

    def para(value, style=text):
        return Paragraph(escape(str(value or '—')).replace('\n', '<br/>'), style)

    def band(title):
        table = Table([[para(title, header)]], colWidths=[width])
        table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), teal), ('TOPPADDING', (0, 0), (-1, -1), 4),
                                   ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
        return table

    grid = [('GRID', (0, 0), (-1, -1), 0.5, line), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]
    story = []
    for index, (_name, data) in enumerate(classes):
        if index:
            story.append(PageBreak())
        story.append(Paragraph(TITLE, big))
        for kind, title, rows in class_sections(**data):
            if kind == 'moments':
                # Relative widths: wide enough for "Duración" in one line, most room for the texts
                widths = [14, 13, 32, 36, 30, 28, 27]
                table = Table([[para(h, label) for h, _ in COLUMNS]] + [[para(v) for v in values] for values in rows],
                              colWidths=[width * w / sum(widths) for w in widths], repeatRows=1)
                table.setStyle(TableStyle(grid + [('BACKGROUND', (0, 0), (-1, 0), soft),
                                                  ('BACKGROUND', (0, 1), (0, -1), soft)]))
                story += [band(title), table, Spacer(1, 8)]
            else:
                table = Table([[para(l, label), para(v, bold if b else text)] for l, v, b in rows],
                              colWidths=[width * 0.24, width * 0.76])
                table.setStyle(TableStyle(grid + [('BACKGROUND', (0, 0), (0, -1), soft)]))
                story += [KeepTogether([band(title), table]), Spacer(1, 8)]
        story.append(Paragraph(NOTE, note))
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.3 * cm,
                      bottomMargin=1.3 * cm, title=TITLE).build(story)
    return buffer.getvalue()


def adapted_docx(classes):
    """The adapted class as a landscape Word document, [(student name, data)]: one student after another."""
    from docx.enum.section import WD_ORIENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    def shade(cell, color):
        properties = cell._tc.get_or_add_tcPr()
        fill = OxmlElement('w:shd')
        fill.set(qn('w:val'), 'clear')
        fill.set(qn('w:fill'), color)
        properties.append(fill)

    def write(cell, value, *, bold=False, color=None):
        cell.text = ''
        run = cell.paragraphs[0].add_run(str(value or '—'))
        run.bold, run.font.size = bold, Pt(9)
        if color:
            run.font.color.rgb = RGBColor.from_string(color)

    def band(title):
        table = document.add_table(rows=1, cols=1)
        write(table.cell(0, 0), title, bold=True, color='FFFFFF')
        shade(table.cell(0, 0), TEAL)

    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    for margin in ('left_margin', 'right_margin', 'top_margin', 'bottom_margin'):
        setattr(section, margin, Cm(1.5))
    for index, (_name, data) in enumerate(classes):
        if index:
            document.add_page_break()
        heading = document.add_paragraph().add_run(TITLE)
        heading.bold, heading.font.size, heading.font.color.rgb = True, Pt(16), RGBColor.from_string(TEAL)
        for kind, title, rows in class_sections(**data):
            band(title)
            if kind == 'moments':
                table = document.add_table(rows=1, cols=len(COLUMNS))
                table.style = 'Table Grid'
                for column, (name, _) in enumerate(COLUMNS):
                    write(table.cell(0, column), name, bold=True, color=TEAL)
                    shade(table.cell(0, column), SOFT)
                for values in rows:
                    cells = table.add_row().cells
                    for column, value in enumerate(values):
                        write(cells[column], value, bold=column == 0, color=TEAL if column == 0 else None)
            else:
                table = document.add_table(rows=0, cols=2)
                table.style = 'Table Grid'
                for label, value, bold in rows:
                    cells = table.add_row().cells
                    write(cells[0], label, bold=True, color=TEAL)
                    shade(cells[0], SOFT)
                    write(cells[1], value, bold=bold)
                    cells[0].width, cells[1].width = Cm(6.5), Cm(20.2)
            document.add_paragraph()
        note = document.add_paragraph().add_run(NOTE)
        note.italic, note.font.size = True, Pt(8)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# The adapted class in the same format the teacher uploaded the plan in
CLASS_FORMATS = {
    'xlsx': (adapted_workbook, '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    'pdf': (adapted_pdf, '.pdf', 'application/pdf'),
    'docx': (adapted_docx, '.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
}


def adapted_class(classes, plan_format):
    """(file bytes, extension, content type) of the adapted class in the format of the teacher's plan."""
    build, extension, content_type = CLASS_FORMATS.get(plan_format, CLASS_FORMATS['xlsx'])
    return build(classes), extension, content_type
