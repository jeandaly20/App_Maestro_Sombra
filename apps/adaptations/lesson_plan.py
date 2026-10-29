"""
The teacher's class plan (Excel) for "Adaptar clase".

Every school has its own format, so nothing here depends on fixed cells: the sessions are found by
their label ("Sesión 30", "Reto 15", "Clase 4") and each session becomes plain "label: value" text
that the AI interprets. Before that text leaves the server, the rows that identify the school,
the teachers or the student (institution, motto, names, signatures, dates, the current
"Adaptación curricular" row with the student's initials) are dropped. The result is returned as a
new Excel with only that session, adapted: the teacher's original file is not copied.
"""
import io
import re
import unicodedata
from datetime import date, datetime

import openpyxl
import openpyxl.utils

SESSION_LABEL = re.compile(r'^\s*(sesi[oó]n|reto|clase)\s*n?[º°.]?\s*\d+', re.IGNORECASE)
# Rows with these words identify people or the school, or are administrative: never sent to the AI
PRIVATE_WORDS = ('docente', 'nombre', 'firma', 'revisado', 'aprobado', 'realizado', 'elaborado', 'unidad educativa',
                 'escuela', 'colegio', 'institucion', 'formato', 'actualiz', 'pagina', 'paralelo', 'fecha', 'periodo',
                 'ano lectivo', 'jefe de area', 'coordinacion', 'rector', 'observaciones', 'bibliografia',
                 'adaptacion curricular', 'estudiante', 'alumno:')
# A plan's pedagogical part starts at its first row with one of these labels (before it: banner, motto)
PEDAGOGICAL_WORDS = ('destreza', 'indicador', 'objetivo', 'eje', 'sesion', 'reto', 'tema', 'calentemos',
                     'inicio', 'anticipacion', 'desarrollo', 'contenido')
URL = re.compile(r'https?://\S+')
MAX_TEXT = 7000


def plain(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower().strip()


def open_workbook(content, *, values=True):
    """values=True reads computed values (for the text); False keeps formulas and styles (to write back)."""
    return openpyxl.load_workbook(io.BytesIO(content), data_only=values)


def _cell_text(value):
    if value is None or isinstance(value, (datetime, date)):
        return ''  # dates identify the school year and the class day: not needed to adapt
    return ' '.join(str(value).split())


def _rows(sheet):
    for row in sheet.iter_rows():
        texts = [_cell_text(cell.value) for cell in row]
        texts = [text for text in texts if text]
        if texts:
            yield row[0].row, texts


def sessions(content):
    """Every session found in the workbook: [{'key', 'sheet', 'row', 'label', 'title'}]."""
    workbook = open_workbook(content)
    found = []
    for sheet in workbook.worksheets:
        sheet_sessions = []
        for number, texts in _rows(sheet):
            label = next((text for text in texts if SESSION_LABEL.match(text)), None)
            if not label:
                continue
            # The title is usually the value after a "Título…" label in the same row
            title = ''
            for index, text in enumerate(texts):
                if plain(text).startswith('titulo') and index + 1 < len(texts):
                    title = texts[index + 1]
            sheet_sessions.append({'key': f'{sheet.title}|{number}', 'sheet': sheet.title, 'row': number,
                                   'label': label, 'title': title})
        if not sheet_sessions and sum(1 for _ in _rows(sheet)) > 5:
            # A plan without numbered sessions: the whole sheet is one class
            sheet_sessions.append({'key': f'{sheet.title}|0', 'sheet': sheet.title, 'row': 0,
                                   'label': 'Hoja completa', 'title': ''})
        found += sheet_sessions
    return found


def _is_private(texts):
    joined = plain(' '.join(texts))
    first = plain(texts[0])
    return any(first.startswith(word) or f' {word}' in f' {first}' for word in PRIVATE_WORDS) or (
        len(texts) == 1 and any(word in joined for word in ('unidad educativa', 'formando', 'lema')))


def session_text(content, sheet_name, row):
    """The chosen session as 'label: value' lines, without personal or school data."""
    sheet = open_workbook(content)[sheet_name]
    rows = list(_rows(sheet))
    starts = [number for number, texts in rows if any(SESSION_LABEL.match(text) for text in texts)]
    pedagogical = next((number for number, texts in rows
                        if any(word in plain(' '.join(texts)) for word in PEDAGOGICAL_WORDS)), 0)
    first_session = starts[0] if starts else None
    end = next((start for start in starts if start > row), None) if row else None
    lines = []
    for number, texts in rows:
        shared = pedagogical <= number < (first_session or 10 ** 9)  # sheet-level fields: skill, indicator…
        in_session = (not row) or (row <= number and (end is None or number < end))
        if not (shared or in_session) or number < pedagogical or _is_private(texts):
            continue
        lines.append(' | '.join(URL.sub('', text).strip() for text in texts if URL.sub('', text).strip()))
    return '\n'.join(line for line in lines if line)[:MAX_TEXT]


# ---------- The adapted class plan as a new, clean Excel ----------

TEAL, SOFT, BORDER = '00685F', 'E6F4F2', 'C9D4D2'
COLUMNS = [('Momento', 16), ('Duración', 11), ('Lo que hace la clase', 34), ('Qué hace el docente sombra', 38),
           ('Qué hace el estudiante', 32), ('Apoyos', 30), ('Qué vigilar', 28)]


def adapted_workbook(*, analysis, content, subject, grade, pseudonym, condition, adaptation_grade, plan_row):
    """
    Only the chosen session, adapted for one student: no institution, teacher or student names
    (the student appears by pseudonym). Built from scratch so nothing of the original file leaks.
    """
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = 'Clase adaptada'
    side = Side(style='thin', color=BORDER)
    border = Border(left=side, right=side, top=side, bottom=side)
    wrap = Alignment(wrap_text=True, vertical='top')
    for index, (_, width) in enumerate(COLUMNS, start=1):
        sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = width
    last = openpyxl.utils.get_column_letter(len(COLUMNS))
    row = 1

    def merged(label, value, *, bold_value=False):
        nonlocal row
        sheet.cell(row, 1, label).font = Font(bold=True, color=TEAL)
        sheet.cell(row, 1).fill = PatternFill('solid', fgColor=SOFT)
        sheet.cell(row, 1).alignment = wrap
        sheet.cell(row, 2, value).alignment = wrap
        sheet.cell(row, 2).font = Font(bold=bold_value)
        sheet.merge_cells(f'B{row}:{last}{row}')
        for column in range(1, len(COLUMNS) + 1):
            sheet.cell(row, column).border = border
        # Rough height so long texts stay readable without opening each cell
        sheet.row_dimensions[row].height = max(18, 15 * (len(str(value)) // 150 + 1))
        row += 1

    def title(text):
        nonlocal row
        cell = sheet.cell(row, 1, text)
        cell.font = Font(bold=True, size=12, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor=TEAL)
        sheet.merge_cells(f'A{row}:{last}{row}')
        row += 1

    sheet.cell(row, 1, 'PLAN DE CLASE ADAPTADO').font = Font(bold=True, size=16, color=TEAL)
    sheet.merge_cells(f'A{row}:{last}{row}')
    row += 2

    title('1. Datos de la clase y del estudiante')
    merged('Materia', subject)
    merged('Clase', analysis.get('title', ''))
    merged('Grado', grade)
    merged('Estudiante', f'{pseudonym} (seudónimo) · {condition}')
    merged('Grado de adaptación', adaptation_grade, bold_value=True)
    merged('Justificación', content.get('grade_reason', ''))
    row += 1

    title('2. Currículo')
    merged('Destreza con criterio de desempeño', analysis.get('skill', ''))
    merged('Indicador de evaluación', analysis.get('indicator', ''))
    merged('Objetivo de la clase', analysis.get('objective', ''))
    merged('Objetivo adaptado', content.get('adapted_objective', ''), bold_value=True)
    row += 1

    title('3. Momentos de la clase adaptados')
    for column, (header, _) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row, column, header)
        cell.font = Font(bold=True, color=TEAL)
        cell.fill = PatternFill('solid', fgColor=SOFT)
        cell.alignment = wrap
        cell.border = border
    row += 1
    originals = {plain(m.get('name')): m.get('activities', '') for m in analysis.get('moments', [])}
    for moment in content.get('moments', []):
        values = [moment.get('name', ''), moment.get('duration', ''), originals.get(plain(moment.get('name')), ''),
                  '\n'.join(f'• {action}' for action in moment.get('teacher_actions', [])),
                  moment.get('student_task', ''), '\n'.join(f'• {support}' for support in moment.get('supports', [])),
                  moment.get('watch_for', '')]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row, column, value)
            cell.alignment = wrap
            cell.border = border
        sheet.row_dimensions[row].height = max(60, 15 * (max(len(str(v)) for v in values) // 30 + 1))
        row += 1
    row += 1

    title('4. Materiales, evaluación y cierre')
    merged('Materiales de la clase', analysis.get('materials', ''))
    merged('Evaluación adaptada', content.get('evaluation', ''))
    merged('Preguntas de cierre', '\n'.join(f'• {q}' for q in content.get('metacognition', [])))
    row += 1

    title('5. Adaptación curricular (para la planificación)')
    merged('Adaptación curricular', plan_row, bold_value=True)
    row += 1
    note = sheet.cell(row, 1, 'Generado con Maestro Sombra. Revise y ajuste la adaptación antes de entregarla.')
    note.font = Font(italic=True, color='5B6765')
    sheet.merge_cells(f'A{row}:{last}{row}')

    sheet.sheet_view.showGridLines = False
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
