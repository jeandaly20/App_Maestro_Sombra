"""
Local check of a photo (or of a scanned PDF page) BEFORE anything of it can be sent to the AI.

The photo is not covered or edited: it is either clean and sent as it is, or it is refused and the teacher is asked
for a page without writing (for example, their own copy of the book). Everything runs on this server with the local
Tesseract reader; the AI never looks at a photo that has not passed this check.

A photo is refused when:
- it cannot be checked: the local reader is not installed, or the page cannot be read clearly enough;
- the app's identifier rules find personal data (names of the teacher's students, ID numbers, e-mails, phones,
  dates, "Nombre: …");
- a personal-data field is filled in (something written after "Nombre:", "Fecha:", "Estudiante:"…);
- there is handwriting in blue pen (thin strokes of blue ink that are not printed text).

Limits: handwriting in pencil, black or red pen away from those fields, faces and signatures are not detected
reliably, so the teacher is also told to upload pages without writing. Safe by default: any error refuses.
"""
import base64
import io
import logging
import re
import subprocess
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFilter, ImageMath, ImageOps

from . import document_privacy

logger = logging.getLogger('maestro_sombra')

MAX_SIDE = 2000             # the photo is read at most this size (enough for printed book text)
READABLE_CONFIDENCE = 80    # a word read with this confidence counts as clearly read
MIN_READABLE_WORDS = 15     # fewer clearly read words: the page cannot be checked, so it is refused
# Blue pen ink. Neat handwriting is read as confidently as print, so it is not excused for being legible: a
# dark-blue printed title may cause a false alarm (the teacher is asked for another photo), never a leak.
INK_MAX_BRIGHTNESS = 150    # pen ink is dark...
INK_MIN_SATURATION = 60     # ...and clearly coloured (the mixed edges of a drawing's fill are not)
INK_MIN_PIXELS = 20         # a few stray pixels are not a handwritten word
MAX_STROKE_DENSITY = 0.45   # handwriting is thin strokes; a filled shape (a leaf, a coloured box) is denser
OCR_TIMEOUT = 60
LABEL = re.compile(r'^(nombres?|apellidos?|estudiante|alumn[oa]|fecha|curso|paralelo|grado|docente|profesora?|'
                   r'representante|c[eé]dula|correo|tel[eé]fono|firma)[:.]?$', re.IGNORECASE)
BLANK = re.compile(r'^[_.\-–—·…:|]+$')  # the printed line of an empty field

UNCHECKABLE = ('No se pudo revisar la foto en este equipo, así que no se envió a la IA. Sube la página en un PDF '
               'digital.')
UNREADABLE = ('La foto no se lee con claridad (borrosa, inclinada, con poca luz o con sombras), así que no se puede '
              'comprobar que no tenga datos personales y no se envió a la IA. Toma la foto de frente, con buena luz '
              'y con la página completa, o sube un PDF digital.')
ASK_CLEAN_PAGE = ('Por privacidad no se envía a la IA. Sube una foto de la página sin escribir (por ejemplo, de tu '
                  'libro del docente o de una copia en blanco) o un PDF digital.')


@dataclass
class PhotoCheck:
    allowed: bool
    reasons: list = field(default_factory=list)
    highlights: list = field(default_factory=list)  # (box, why) on the prepared image, to show the teacher
    image: Image.Image = None                        # the prepared photo (what would be sent)

    @property
    def message(self):
        return ' '.join(self.reasons)

    def preview(self, max_side=700):
        """The photo with what was detected framed in red, as a data URI for the page. Never stored or sent."""
        if self.image is None or not self.highlights:
            return ''
        picture = self.image.copy()
        draw = ImageDraw.Draw(picture)
        for (x0, y0, x1, y1), _why in self.highlights:
            draw.rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), outline=(220, 30, 30), width=6)
        picture.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        picture.convert('RGB').save(buffer, 'JPEG', quality=80)
        return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()


def prepared(image):
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


def _skew(gray):
    """The rotation that makes the text lines most horizontal (projection profile on a small binarized copy)."""
    small = gray.copy()
    small.thumbnail((600, 600))
    binary = small.point(lambda v: 0 if v < 140 else 255)

    def sharpness(angle):
        rotated = binary.rotate(angle, fillcolor=255, resample=Image.BILINEAR)
        rows = list(rotated.resize((1, rotated.height), Image.BOX).getdata())
        mean = sum(rows) / len(rows)
        return sum((r - mean) ** 2 for r in rows)
    return max((a / 2 for a in range(-16, 17)), key=sharpness)


def _even(gray):
    """Shadows evened out (each pixel against the blurred page background), then the contrast stretched."""
    background = gray.filter(ImageFilter.GaussianBlur(30))
    evaluate = getattr(ImageMath, 'unsafe_eval', None) or ImageMath.eval
    flat = evaluate('convert(min(a * 235 / (b + 1), 255), "L")', a=gray, b=background)
    return ImageOps.autocontrast(flat, cutoff=1)


def _ocr(image):
    """Words with their position and confidence, read by the local Tesseract. None if it is not installed."""
    from apps.students.documents import tesseract
    found = tesseract()
    if not found:
        return None
    base, language = found
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    result = subprocess.run(base + ['stdin', 'stdout', '-l', language, '--psm', '3', '-c', 'tessedit_create_tsv=1'],
                            input=buffer.getvalue(), capture_output=True, timeout=OCR_TIMEOUT)
    words = []
    for line in result.stdout.decode('utf-8', errors='ignore').splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) == 12 and parts[11].strip():
            left, top, width, height = (int(v) for v in parts[6:10])
            words.append({'line': tuple(parts[2:5]), 'box': (left, top, left + width, top + height),
                          'conf': float(parts[10]), 'text': parts[11].strip()})
    return words


def _rotate_box(box, angle, size):
    """A box found on the straightened image, back on the photo's own geometry (its enclosing rectangle)."""
    import math
    cx, cy = size[0] / 2, size[1] / 2
    a = math.radians(angle)  # PIL rotates counter-clockwise by `angle`; undo it
    points = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        dx, dy = x - cx, y - cy
        points.append((cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)))
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


def _coloured_ink(image):
    """Boxes of blue pen strokes (dark, saturated, blue) shaped like handwritten words."""
    # Thin strokes keep their colour when the copy is reduced: each pixel first takes the darkest of its neighbours
    small = image.filter(ImageFilter.MinFilter(3))
    small.thumbnail((620, 620))
    sx, sy = image.width / small.width, image.height / small.height
    pixels = small.load()
    ink = Image.new('L', small.size, 0)
    ink_px = ink.load()
    for y in range(small.height):
        for x in range(small.width):
            r, g, b = pixels[x, y]
            # Blue pen ink: dark, saturated and blue-dominant (green leaves, orange borders, teal titles are not)
            if (max(r, g, b) < INK_MAX_BRIGHTNESS and max(r, g, b) - min(r, g, b) > INK_MIN_SATURATION
                    and b > r + 25 and b > g + 10):
                ink_px[x, y] = 255
    raw = ink.load()
    drawing = _blue_drawings(small)
    px = ink.filter(ImageFilter.MaxFilter(7)).load()
    boxes, seen = [], set()
    for y in range(0, small.height, 2):
        for x in range(0, small.width, 2):
            if not px[x, y] or (x, y) in seen:
                continue
            stack, xs, ys = [(x, y)], [], []
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in seen or not (0 <= cx < small.width and 0 <= cy < small.height) or not px[cx, cy]:
                    continue
                seen.add((cx, cy))
                xs.append(cx)
                ys.append(cy)
                stack += [(cx + 2, cy), (cx - 2, cy), (cx, cy + 2), (cx, cy - 2)]
            if len(xs) < INK_MIN_PIXELS:
                continue
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            strokes = sum(1 for yy in range(y0, y1 + 1) for xx in range(x0, x1 + 1) if raw[xx, yy])
            if strokes / ((x1 - x0 + 1) * (y1 - y0 + 1)) > MAX_STROKE_DENSITY:
                continue  # a filled shape of a drawing, not handwritten strokes
            box = (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))
            height, width = box[3] - box[1], box[2] - box[0]
            if not (height < 0.06 * image.height and width > 1.3 * height):
                continue  # filled shapes and drawings are not handwritten words
            on_drawing = sum(1 for xx, yy in zip(xs, ys) if (xx // 2 * 2, yy // 2 * 2) in drawing)
            if on_drawing > 0.5 * len(xs):
                continue  # a stretch of a large printed line drawing (a mandala, a table's blue lines)
            boxes.append(box)
    return boxes


DRAWING_MIN_WIDTH, DRAWING_MIN_HEIGHT = 0.12, 0.08  # share of the page: a blue figure this big in both directions


def _blue_drawings(small):
    """
    The points of large printed blue line drawings. Seen with a wider blue tolerance, a drawing's lines join into
    one figure that is big both across and down the page; a handwritten word stays small (a line may be long,
    but it is low). Strokes that belong to such a figure are not handwriting; writing inside it is not joined to
    its lines, so it is still detected.
    """
    pixels = small.load()
    loose = Image.new('L', small.size, 0)
    loose_px = loose.load()
    for y in range(small.height):
        for x in range(small.width):
            r, g, b = pixels[x, y]
            if b > r + 15 and b > g + 5 and max(r, g, b) - min(r, g, b) > 30 and max(r, g, b) < 230:
                loose_px[x, y] = 255
    px = loose.filter(ImageFilter.MaxFilter(3)).load()
    points, seen = set(), set()
    for y in range(0, small.height, 2):
        for x in range(0, small.width, 2):
            if not px[x, y] or (x, y) in seen:
                continue
            stack, component = [(x, y)], []
            while stack:
                cx, cy = stack.pop()
                if (cx, cy) in seen or not (0 <= cx < small.width and 0 <= cy < small.height) or not px[cx, cy]:
                    continue
                seen.add((cx, cy))
                component.append((cx, cy))
                stack += [(cx + 2, cy), (cx - 2, cy), (cx, cy + 2), (cx, cy - 2)]
            xs, ys = [p[0] for p in component], [p[1] for p in component]
            if (max(xs) - min(xs) >= DRAWING_MIN_WIDTH * small.width
                    and max(ys) - min(ys) >= DRAWING_MIN_HEIGHT * small.height):
                points.update(component)
    return points


def check_image(image, names=()):
    """Whether this photo may be sent to the AI as it is. Any error refuses it."""
    try:
        image = prepared(image)
        gray = image.convert('L')
        angle = _skew(gray)
        straight = _even(gray.rotate(angle, fillcolor=255, resample=Image.BICUBIC))
        words = _ocr(straight)
        if words is None:
            return PhotoCheck(False, [UNCHECKABLE], image=image)
        readable = [w for w in words if w['conf'] >= READABLE_CONFIDENCE and sum(c.isalpha() for c in w['text']) >= 3]
        if len(readable) < MIN_READABLE_WORDS:
            return PhotoCheck(False, [UNREADABLE], image=image)
        found, highlights = set(), []
        lines = {}
        for word in words:
            lines.setdefault(word['line'], []).append(word)
        for line_words in lines.values():
            line_words.sort(key=lambda w: w['box'][0])
            text, offsets = '', []
            for word in line_words:
                offsets.append((len(text), len(text) + len(word['text'])))
                text += word['text'] + ' '
            for start, end, kind in document_privacy._spans(text, names):
                found.add(document_privacy.KIND_NAMES.get(kind, 'datos personales'))
                highlights += [(_rotate_box(w['box'], angle, image.size), kind)
                               for w, (a, b) in zip(line_words, offsets) if a < end and b > start]
            for index, word in enumerate(line_words):
                if not LABEL.match(word['text']):
                    continue
                after = [w for w in line_words[index + 1:index + 4] if not BLANK.match(w['text'])
                         and not LABEL.match(w['text'])]
                if after:
                    found.add('campos de datos personales llenos (Nombre, Fecha…)')
                    highlights.append((_rotate_box((word['box'][0], word['box'][1], after[-1]['box'][2],
                                                    max(w['box'][3] for w in after)), angle, image.size), 'campo lleno'))

        ink = _coloured_ink(image)
        if ink:
            found.add('escritura a mano')
            highlights += [(box, 'escritura a mano') for box in ink]
        if found:
            detected = ', '.join(sorted(found))
            return PhotoCheck(False, [f'Detectamos {detected} en la foto (marcado en rojo).', ASK_CLEAN_PAGE],
                              highlights=highlights, image=image)
        return PhotoCheck(True, image=image)
    except Exception:  # noqa: BLE001 - safe by default: an error never lets the photo through
        logger.warning('Photo check failed', exc_info=False)
        return PhotoCheck(False, [UNCHECKABLE])
