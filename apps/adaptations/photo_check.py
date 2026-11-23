"""
Local check of a photo (or of a scanned PDF page) BEFORE anything of it can be sent to the AI.

The photo is not covered or edited: it is either sent as it is, or it is refused and the teacher is asked to cover
the name or take another photo. Everything runs on this server with the local Tesseract reader.

A photo is refused only when the FULL name of one of the teacher's registered students appears in it (at least two
words of the same registered name together, e.g. "Mateo Benítez"). A first name alone ("Mateo compra 3 lápices")
is taken as the example of the book's exercise, and the page is sent. Printed colours, fonts, drawings and filled-in
fields are not judged, so a clean class page is never refused for how it looks.

If the local reader is not installed or the check fails, the photo is not sent (nothing could be verified).
"""
import base64
import io
import logging
import re
import subprocess  # nosec B404 - only fixed local tools (Tesseract, pg_dump), never a shell
import unicodedata
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFilter, ImageMath, ImageOps

logger = logging.getLogger('maestro_sombra')

MAX_SIDE = 2000             # the photo is read at most this size (enough for printed book text)
OCR_TIMEOUT = 60
NAME_PARTICLES = {'de', 'del', 'la', 'las', 'los', 'y'}  # "María de los Ángeles": not name words on their own

UNCHECKABLE = ('No se pudo revisar la foto en este equipo, así que no se envió a la IA. Sube la página en un PDF '
               'digital.')
ASK_CLEAN_PAGE = ('Por privacidad no se envía a la IA. Tapa el nombre o sube otra foto de la página; los nombres de '
                  'ejemplo del libro no son problema.')
FOUND_NAME = 'Detectamos el nombre completo de un estudiante registrado en la foto (marcado en rojo).'


def _plain(word):
    """A word without accents, punctuation or case, to compare names as the reader sees them."""
    word = unicodedata.normalize('NFKD', word)
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in word if not unicodedata.combining(c)).lower())


def _name_words(names):
    """For each registered name, the set of its words that identify (no particles, at least 3 letters)."""
    result = []
    for name in names:
        words = {_plain(w) for w in str(name).split()}
        words = {w for w in words if len(w) >= 3 and w not in NAME_PARTICLES}
        if len(words) >= 2:
            result.append(words)
    return result


def full_names(line_words, names):
    """Indexes of the words in this line that form a full registered name: two different words of the same name
    next to each other (a particle such as "de" may sit between them)."""
    plain = [_plain(w['text']) for w in line_words]
    marked = set()
    for words in _name_words(names):
        for i, first in enumerate(plain):
            if first not in words:
                continue
            j = i + 1
            while j < len(plain) and plain[j] in NAME_PARTICLES:
                j += 1
            if j < len(plain) and plain[j] in words and plain[j] != first:
                marked.update(range(i, j + 1))
    return sorted(marked)


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
        rows = list(rotated.resize((1, rotated.height), Image.BOX).get_flattened_data())
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
    result = subprocess.run(base + ['stdin', 'stdout', '-l', language, '--psm', '3', '-c', 'tessedit_create_tsv=1'],  # nosec B603 - argument list, no shell; the executable is Tesseract and the input an image
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


def check_image(image, names=()):
    """Whether this photo may be sent to the AI as it is: refused only for a registered student's full name."""
    try:
        image = prepared(image)
        if not _name_words(names):
            return PhotoCheck(True, image=image)  # no full name to look for
        gray = image.convert('L')
        angle = _skew(gray)
        straight = _even(gray.rotate(angle, fillcolor=255, resample=Image.BICUBIC))
        words = _ocr(straight)
        if words is None:
            return PhotoCheck(False, [UNCHECKABLE], image=image)
        lines = {}
        for word in words:
            lines.setdefault(word['line'], []).append(word)
        highlights = []
        for line_words in lines.values():
            line_words.sort(key=lambda w: w['box'][0])
            marked = full_names(line_words, names)
            if marked:
                box = (min(line_words[i]['box'][0] for i in marked), min(line_words[i]['box'][1] for i in marked),
                       max(line_words[i]['box'][2] for i in marked), max(line_words[i]['box'][3] for i in marked))
                highlights.append((_rotate_box(box, angle, image.size), 'nombre'))
        if highlights:
            return PhotoCheck(False, [FOUND_NAME, ASK_CLEAN_PAGE], highlights=highlights, image=image)
        return PhotoCheck(True, image=image)
    except Exception:  # noqa: BLE001 - an error never lets an unchecked photo through
        logger.warning('Photo check failed', exc_info=False)
        return PhotoCheck(False, [UNCHECKABLE])
