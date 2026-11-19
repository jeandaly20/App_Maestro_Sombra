"""
The documents the teacher hands to the institution (DECE, coordination, representatives) in APA 7th edition style:
Times New Roman 12 in black, 2.54 cm margins, double-spaced text with indented paragraphs, centered bold level 1
headings, flush-left bold level 2 headings, numbered tables and figures with an italic title and only horizontal
rules, and the page number at the top right.

The materials for the student (the adapted worksheet) do not use it: they keep their accessible font and supports.
"""
import functools
import io
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MARGIN = 2.54 * cm                   # one inch on every side
WIDTH = A4[0] - 2 * MARGIN
SIZE, DOUBLE, SINGLE = 12, 24, 14    # font size, double spacing (text) and single spacing (inside tables)
INDENT = 1.27 * cm                   # first line of each paragraph
SPLIT_AFTER = 4                      # a table with more rows may continue on the next page
BLACK = colors.black
# Times New Roman where the server has it (Windows, or Linux with the Microsoft core fonts); otherwise the PDF's own
# Times, with the same metrics
FONT_FILES = [
    (Path('C:/Windows/Fonts'), ('times.ttf', 'timesbd.ttf', 'timesi.ttf', 'timesbi.ttf')),
    (Path('/usr/share/fonts/truetype/msttcorefonts'),
     ('Times_New_Roman.ttf', 'Times_New_Roman_Bold.ttf', 'Times_New_Roman_Italic.ttf',
      'Times_New_Roman_Bold_Italic.ttf')),
]


@functools.lru_cache(maxsize=1)
def fonts():
    """(regular, bold, italic, bold italic, embedded): Times New Roman embedded when available."""
    for folder, files in FONT_FILES:
        paths = [folder / name for name in files]
        if all(path.exists() for path in paths):
            names = ('TimesNewRoman', 'TimesNewRoman-Bold', 'TimesNewRoman-Italic', 'TimesNewRoman-BoldItalic')
            for name, path in zip(names, paths):
                pdfmetrics.registerFont(TTFont(name, str(path)))
            for (bold, italic), name in zip(((0, 0), (1, 0), (0, 1), (1, 1)), names):
                addMapping('TimesNewRoman', bold, italic, name)
            return (*names, True)
    return 'Times-Roman', 'Times-Bold', 'Times-Italic', 'Times-BoldItalic', False


def drawable(value):
    """The PDF's own Times has no minus sign: a hyphen takes its place there."""
    return str(value) if fonts()[4] else str(value).replace('−', '-')


def clean(value):
    """Text for a paragraph: escaped, with line breaks kept."""
    return drawable(escape(str(value if value not in (None, '') else '—')).replace('\n', '<br/>'))


@functools.lru_cache(maxsize=1)
def styles():
    regular, bold, italic, _bold_italic, _embedded = fonts()
    base = ParagraphStyle('apa', fontName=regular, fontSize=SIZE, leading=DOUBLE, textColor=BLACK)
    return {
        'title': ParagraphStyle('title', parent=base, fontName=bold, alignment=TA_CENTER),
        'subtitle': ParagraphStyle('subtitle', parent=base, alignment=TA_CENTER),
        'h1': ParagraphStyle('h1', parent=base, fontName=bold, alignment=TA_CENTER, spaceBefore=12),
        'h2': ParagraphStyle('h2', parent=base, fontName=bold),
        'body': ParagraphStyle('body', parent=base, firstLineIndent=INDENT, alignment=TA_JUSTIFY),
        'plain': ParagraphStyle('plain', parent=base),
        'center': ParagraphStyle('center', parent=base, alignment=TA_CENTER),
        'number': ParagraphStyle('number', parent=base, fontName=bold, spaceBefore=12),
        'caption': ParagraphStyle('caption', parent=base, fontName=italic),
        'cell': ParagraphStyle('cell', parent=base, leading=SINGLE),
        'cell_bold': ParagraphStyle('cell_bold', parent=base, fontName=bold, leading=SINGLE),
        'cell_center': ParagraphStyle('cell_center', parent=base, leading=SINGLE, alignment=TA_CENTER),
    }


def paragraph(value, style='cell'):
    return Paragraph(clean(value), styles()[style])


def _page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont(fonts()[0], SIZE)
    canvas.drawRightString(A4[0] - MARGIN, A4[1] - MARGIN / 2, str(doc.page))
    canvas.restoreState()


class ApaDocument:
    """A document built section by section; tables and figures are numbered in order."""

    def __init__(self, title, subtitle=''):
        self.title = title
        self.story = [paragraph(title, 'title')]
        if subtitle:
            self.story.append(paragraph(subtitle, 'subtitle'))
        self.story.append(Spacer(1, 6))
        self.tables = self.figures = 0

    # ----- text -----
    def heading(self, text, level=1):
        self.story.append(paragraph(text, 'h1' if level == 1 else 'h2'))

    def text(self, value):
        """A body paragraph: double-spaced, first line indented."""
        self.story.append(paragraph(value, 'body'))

    def note(self, value):
        self.story.append(Paragraph(f'<i>Nota.</i> {clean(value)}', styles()['plain']))

    # ----- tables and figures -----
    def _numbered(self, kind, title, body, note):
        block = [paragraph(f'{kind}', 'number'), paragraph(title, 'caption'), body]
        if note:
            block.append(Paragraph(f'<i>Nota.</i> {clean(note)}', styles()['plain']))
        self.story.append(KeepTogether(block))

    def table(self, title, head, rows, widths, note='', bold_first=False):
        """
        An APA table: rules above and below the header and at the end, nothing else. A long table may continue on
        the next page (APA allows it): its number, title and first rows stay together, and so do its last row and
        note, so no page is left half empty waiting for the whole table.
        """
        self.tables += 1
        header = [[paragraph(h, 'cell_bold') for h in head]] if head else []
        body = [[paragraph(v, 'cell_bold' if bold_first and i == 0 else 'cell') for i, v in enumerate(row)]
                for row in rows]
        widths = [WIDTH * w for w in widths]

        def part(data, top, bottom, with_header=False):
            table = Table(data, colWidths=widths)
            style = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 4),
                     ('RIGHTPADDING', (0, 0), (-1, -1), 4), ('TOPPADDING', (0, 0), (-1, -1), 4),
                     ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]
            if top:
                style.append(('LINEABOVE', (0, 0), (-1, 0), 1, BLACK))
            if with_header:
                style.append(('LINEBELOW', (0, 0), (-1, 0), 0.6, BLACK))
            if bottom:
                style.append(('LINEBELOW', (0, -1), (-1, -1), 1, BLACK))
            table.setStyle(TableStyle(style))
            return table

        caption = [paragraph(f'Tabla {self.tables}', 'number'), paragraph(title, 'caption')]
        closing = [Paragraph(f'<i>Nota.</i> {clean(note)}', styles()['plain'])] if note else []
        if len(body) <= SPLIT_AFTER:
            self.story.append(KeepTogether(caption + [part(header + body, True, True, bool(header))] + closing))
            return
        first, middle, last = body[:2], body[2:-1], body[-1:]
        self.story.append(KeepTogether(caption + [part(header + first, True, False, bool(header))]))
        if middle:
            self.story.append(part(middle, False, False))
        self.story.append(KeepTogether([part(last, False, True)] + closing))

    def fields(self, title, rows, note=''):
        """A two-column table of label and value (no header)."""
        self.table(title, None, rows, (0.38, 0.62), note, bold_first=True)

    def line_chart(self, title, levels, points, names, note=''):
        """A figure in black: `levels` from the lowest, `points` as indexes into them, one per name."""
        self.figures += 1
        regular = fonts()[0]
        height, left, right, top, bottom = 5.2 * cm, 3.6 * cm, 0.6 * cm, 0.3 * cm, 1.0 * cm
        drawing = Drawing(WIDTH, height)
        ys = [bottom + i * (height - top - bottom) / max(len(levels) - 1, 1) for i in range(len(levels))]
        for y, label in zip(ys, levels):
            drawing.add(Line(left, y, WIDTH - right, y, strokeColor=colors.HexColor('#BBBBBB'), strokeWidth=0.4))
            drawing.add(String(left - 6, y - 3.5, drawable(label), fontName=regular, fontSize=10, fillColor=BLACK,
                               textAnchor='end'))
        span = WIDTH - left - right
        xs = [left + (i * span / (len(points) - 1) if len(points) > 1 else span / 2) for i in range(len(points))]
        xy = [(x, ys[value]) for x, value in zip(xs, points)]
        if len(xy) > 1:
            drawing.add(PolyLine([c for point in xy for c in point], strokeColor=BLACK, strokeWidth=1.2))
        for (x, y), name in zip(xy, names):
            drawing.add(Circle(x, y, 3, fillColor=BLACK, strokeColor=BLACK))
            drawing.add(String(x, 4, drawable(name), fontName=regular, fontSize=10, fillColor=BLACK,
                               textAnchor='middle'))
        self._numbered(f'Figura {self.figures}', title, drawing, note)

    # ----- end -----
    def signatures(self, *labels, keep=1):
        """Signature lines that travel with the last `keep` items, so they never stand alone on a page."""
        cells = [paragraph(label, 'cell_center') for label in labels]
        if len(cells) == 2:
            table = Table([['', '', ''], [cells[0], '', cells[1]]],
                          colWidths=[WIDTH * 0.44, WIDTH * 0.12, WIDTH * 0.44], rowHeights=[1.8 * cm, None])
            lines = [('LINEBELOW', (0, 0), (0, 0), 0.8, BLACK), ('LINEBELOW', (2, 0), (2, 0), 0.8, BLACK)]
        else:
            table = Table([[''], [cells[0]]], colWidths=[WIDTH * 0.5], rowHeights=[1.8 * cm, None])
            lines = [('LINEBELOW', (0, 0), (0, 0), 0.8, BLACK)]
        table.setStyle(TableStyle(lines))
        tail = []
        for _ in range(min(keep, len(self.story))):
            item = self.story.pop()
            tail[:0] = list(item._content) if isinstance(item, KeepTogether) else [item]
        self.story.append(KeepTogether(tail + [Spacer(1, 12), table]))

    def _glued(self):
        """A heading never stays alone at the bottom of a page: it travels with what follows it."""
        out = []
        for item in self.story:
            while (out and isinstance(out[-1], Paragraph) and out[-1].style.name in ('h1', 'h2')
                   and not (isinstance(item, Paragraph) and item.style.name in ('h1', 'h2'))):
                heading = out.pop()
                content = list(item._content) if isinstance(item, KeepTogether) else [item]
                item = KeepTogether([heading] + content)
            out.append(item)
        return out

    def pdf(self):
        buffer = io.BytesIO()
        SimpleDocTemplate(buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                          bottomMargin=MARGIN, title=self.title).build(
            self._glued(), onFirstPage=_page_number, onLaterPages=_page_number)
        return buffer.getvalue()
