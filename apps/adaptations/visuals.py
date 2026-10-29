"""
Visual supports drawn by the system (not by the AI).

The AI only chooses the type of support and its numbers, taken from the worksheet
(e.g. a circle in 4 parts with 3 shaded). This module draws it, so the picture always
matches the data exactly. The approved scope excludes AI-generated artwork, and the
free Gemini API has no image-generation quota.
"""
import base64
import io
import math

from django.contrib.staticfiles import finders
from PIL import Image, ImageDraw, ImageFont

TYPES = ['NONE', 'FRACTION_CIRCLE', 'FRACTION_BAR', 'NUMBER_LINE', 'COUNT', 'SEQUENCE', 'MULTIPLICATION_GRID']
TYPE_LABELS = {
    'FRACTION_CIRCLE': 'Círculo de fracciones',
    'FRACTION_BAR': 'Barra de fracciones',
    'NUMBER_LINE': 'Recta numérica',
    'COUNT': 'Objetos para contar',
    'SEQUENCE': 'Secuencia de pasos',
    'MULTIPLICATION_GRID': 'Cuadrícula de multiplicación',
}

TEAL = (0, 104, 95)
SHADE = (242, 177, 52)
CROSS = (186, 26, 26)
INK = (17, 28, 45)
LIGHT = (240, 243, 255)
WIDTH, HEIGHT = 720, 320


def _font(size, bold=False):
    path = finders.find('fonts/AtkinsonHyperlegible-Bold.ttf' if bold else 'fonts/AtkinsonHyperlegible-Regular.ttf')
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


def _int(value, low, high, default=0):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def clean_visual(spec):
    """Validates the AI's choice. Returns a safe spec, or None if there is nothing to draw."""
    if not isinstance(spec, dict) or spec.get('type') not in TYPES or spec.get('type') == 'NONE':
        return None
    kind = spec['type']
    if kind in ('FRACTION_CIRCLE', 'FRACTION_BAR'):
        parts = _int(spec.get('parts'), 2, 12, 0)
        if not parts:
            return None
        shaded = _int(spec.get('shaded'), 0, parts)
        crossed = _int(spec.get('crossed'), 0, shaded)
        return {'type': kind, 'parts': parts, 'shaded': shaded, 'crossed': crossed}
    if kind == 'NUMBER_LINE':
        start = _int(spec.get('start'), -50, 100, 0)
        end = _int(spec.get('end'), start + 1, start + 20, start + 10)
        marks = [m for m in (spec.get('marks') or []) if isinstance(m, int) and start <= m <= end][:5]
        return {'type': kind, 'start': start, 'end': end, 'marks': marks}
    if kind == 'COUNT':
        count = _int(spec.get('count'), 1, 20, 0)
        if not count:
            return None
        return {'type': kind, 'count': count, 'crossed': _int(spec.get('crossed'), 0, count)}
    if kind == 'MULTIPLICATION_GRID':
        # Factors of 2 digits, as in the worksheets that multiply by decomposing into tens and units
        a, b = _int(spec.get('a'), 10, 99, 0), _int(spec.get('b'), 10, 99, 0)
        if not (a and b):
            return None
        return {'type': kind, 'a': a, 'b': b, 'show_products': bool(spec.get('show_products'))}
    items = [str(item).strip()[:40] for item in (spec.get('items') or []) if str(item).strip()][:5]
    return {'type': 'SEQUENCE', 'items': items} if len(items) >= 2 else None


def _cross(draw, box):
    x0, y0, x1, y1 = box
    draw.line([x0, y0, x1, y1], fill=CROSS, width=6)
    draw.line([x0, y1, x1, y0], fill=CROSS, width=6)


def _fraction_circle(draw, spec):
    cx, cy, r = 200, HEIGHT // 2, 125
    step = 360 / spec['parts']
    for i in range(spec['parts']):
        start = -90 + i * step
        fill = SHADE if i < spec['shaded'] else 'white'
        draw.pieslice([cx - r, cy - r, cx + r, cy + r], start, start + step, fill=fill, outline=INK, width=4)
        if i < spec['crossed']:
            angle = math.radians(start + step / 2)
            px, py = cx + r * 0.6 * math.cos(angle), cy + r * 0.6 * math.sin(angle)
            _cross(draw, (px - 18, py - 18, px + 18, py + 18))
    _fraction_label(draw, spec, 420)


def _fraction_bar(draw, spec):
    x0, y0, total, height = 40, 90, 420, 110
    cell = total / spec['parts']
    for i in range(spec['parts']):
        box = (x0 + i * cell, y0, x0 + (i + 1) * cell, y0 + height)
        draw.rectangle(box, fill=SHADE if i < spec['shaded'] else 'white', outline=INK, width=4)
        if i < spec['crossed']:
            _cross(draw, (box[0] + 12, box[1] + 25, box[2] - 12, box[3] - 25))
    _fraction_label(draw, spec, 500)


def _fraction_label(draw, spec, x):
    big, small = _font(64, bold=True), _font(26)
    kept = spec['shaded'] - spec['crossed']
    draw.text((x, 70), str(kept if spec['crossed'] else spec['shaded']), font=big, fill=TEAL)
    draw.line([x, 150, x + 90, 150], fill=TEAL, width=5)
    draw.text((x, 155), str(spec['parts']), font=big, fill=TEAL)
    caption = (f'{spec["shaded"]} pintadas, {spec["crossed"]} tachadas' if spec['crossed']
               else f'{spec["shaded"]} de {spec["parts"]} partes')
    draw.text((x - 20, 250), caption, font=small, fill=INK)


def _number_line(draw, spec):
    x0, x1, y = 50, WIDTH - 50, HEIGHT // 2
    draw.line([x0, y, x1, y], fill=INK, width=5)
    count = spec['end'] - spec['start']
    font = _font(24)
    for i in range(count + 1):
        x = x0 + i * (x1 - x0) / count
        value = spec['start'] + i
        marked = value in spec['marks']
        draw.line([x, y - 18, x, y + 18], fill=TEAL if marked else INK, width=6 if marked else 3)
        if marked:
            draw.ellipse([x - 16, y - 60, x + 16, y - 28], fill=SHADE, outline=INK, width=3)
        draw.text((x - 10, y + 26), str(value), font=font, fill=INK)


def _count(draw, spec):
    per_row = 10
    size, gap = 48, 14
    for i in range(spec['count']):
        row, col = divmod(i, per_row)
        x = 40 + col * (size + gap)
        y = 60 + row * (size + gap + 20)
        draw.ellipse([x, y, x + size, y + size], fill=SHADE, outline=INK, width=3)
        if i >= spec['count'] - spec['crossed']:
            _cross(draw, (x + 4, y + 4, x + size - 4, y + size - 4))
    caption = f'Total: {spec["count"]}'
    if spec['crossed']:
        caption += f'   Quitamos: {spec["crossed"]}   Quedan: {spec["count"] - spec["crossed"]}'
    draw.text((40, HEIGHT - 60), caption, font=_font(28, bold=True), fill=TEAL)


def _sequence(draw, spec):
    items = spec['items']
    words = ['Primero', 'Después', 'Luego', 'Después', 'Al final']
    labels = words[:len(items) - 1] + ['Al final']
    box_w = (WIDTH - 40 - 30 * (len(items) - 1)) / len(items)
    title_font, text_font = _font(24, bold=True), _font(20)
    for i, (label, text) in enumerate(zip(labels, items)):
        x0 = 20 + i * (box_w + 30)
        draw.rounded_rectangle([x0, 70, x0 + box_w, 250], radius=16, fill=LIGHT, outline=TEAL, width=4)
        draw.text((x0 + 12, 82), label, font=title_font, fill=TEAL)
        line, y = '', 122
        for word in text.split():
            trial = f'{line} {word}'.strip()
            if draw.textlength(trial, font=text_font) > box_w - 24:
                draw.text((x0 + 12, y), line, font=text_font, fill=INK)
                line, y = word, y + 28
            else:
                line = trial
        draw.text((x0 + 12, y), line, font=text_font, fill=INK)
        if i < len(items) - 1:
            ax = x0 + box_w + 4
            draw.polygon([(ax, 150), (ax + 22, 160), (ax, 170)], fill=TEAL)


def _multiplication_grid(draw, spec):
    """a × b split into tens and units: the factors on the edges, the partial products inside (or empty)."""
    a_parts, b_parts = [spec['a'] // 10 * 10, spec['a'] % 10], [spec['b'] // 10 * 10, spec['b'] % 10]
    cell, edge = 92, 64
    x0, y0 = 110, 22
    label, number = _font(30, bold=True), _font(30)
    # Edges: the second factor on top (orange), the first one on the left
    for column, value in enumerate(b_parts):
        box = [x0 + edge + column * cell, y0, x0 + edge + (column + 1) * cell, y0 + edge]
        draw.rounded_rectangle(box, radius=10, outline=SHADE, width=5)
        draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), str(value), font=label, fill=INK, anchor='mm')
    for row, value in enumerate(a_parts):
        box = [x0, y0 + edge + row * cell, x0 + edge, y0 + edge + (row + 1) * cell]
        draw.rounded_rectangle(box, radius=10, outline=SHADE, width=5)
        draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), str(value), font=label, fill=INK, anchor='mm')
    products = []
    for row, left in enumerate(a_parts):
        for column, top in enumerate(b_parts):
            box = [x0 + edge + column * cell, y0 + edge + row * cell, x0 + edge + (column + 1) * cell,
                   y0 + edge + (row + 1) * cell]
            draw.rectangle(box, outline=(49, 107, 243), width=5)
            if spec['show_products']:
                draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), str(left * top), font=number,
                          fill=TEAL, anchor='mm')
            products.append(left * top)
    text_x = x0 + edge + 2 * cell + 40
    draw.text((text_x, 70), f'{spec["a"]} × {spec["b"]}', font=_font(40, bold=True), fill=TEAL)
    if spec['show_products']:
        draw.text((text_x, 140), ' + '.join(str(p) for p in products if p), font=_font(24), fill=INK)
        draw.text((text_x, 180), f'= {spec["a"] * spec["b"]}', font=_font(34, bold=True), fill=INK)
    else:
        draw.text((text_x, 140), 'Multiplica cada fila', font=_font(24), fill=INK)
        draw.text((text_x, 174), 'por cada columna y', font=_font(24), fill=INK)
        draw.text((text_x, 208), 'suma los resultados.', font=_font(24), fill=INK)


RENDERERS = {
    'FRACTION_CIRCLE': _fraction_circle, 'FRACTION_BAR': _fraction_bar,
    'NUMBER_LINE': _number_line, 'COUNT': _count, 'SEQUENCE': _sequence,
    'MULTIPLICATION_GRID': _multiplication_grid,
}


def render_png(spec):
    image = Image.new('RGB', (WIDTH, HEIGHT), 'white')
    RENDERERS[spec['type']](ImageDraw.Draw(image), spec)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG', optimize=True)
    return buffer.getvalue()


def data_uri(spec):
    """PNG as a data: URI, used both on screen and inside the PDF."""
    return 'data:image/png;base64,' + base64.b64encode(render_png(spec)).decode()
