"""
Photos of book pages: checked locally (photo_check) before anything is sent to the AI. A clean page is sent as it is;
a page with personal data, a filled-in name or date field, blue-pen handwriting, or that cannot be read is refused,
and the teacher sees what was detected. Made-up pages; Gemini simulated. Needs the local Tesseract reader.
"""
import io
import math
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from apps.accounts.models import Teacher
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.documents import tesseract
from apps.students.models import Student

from . import ai, document_privacy, photo_check, services
from .forms import AdaptMaterialForm
from .tests import FakeGemini

FONTS = Path('C:/Windows/Fonts')
PRINT, BOLD, HAND = FONTS / 'arial.ttf', FONTS / 'arialbd.ttf', FONTS / 'Inkfree.ttf'
NAME = 'Ximena Quilumba Tixe'
EXERCISE = ['Lee el texto y responde las preguntas.', 'El perro de Ana juega en el parque con su pelota.',
            'Su dueño lo cuida mucho y lo lleva al veterinario.', '1. Subraya los sustantivos del texto.',
            '2. Escribe tres sustantivos comunes en tu cuaderno.', '3. Clasifica las palabras: perro, Quito, mesa.',
            '4. Escribe una oración con un sustantivo propio.']
READY = tesseract() is not None and PRINT.exists() and HAND.exists()


def blue_drawing(draw, cx, cy, r, answer=False):
    """A mandala printed in thin blue lines, like the book's; optionally an answer written in blue pen inside."""
    blue = (55, 110, 175)
    for k in (1.0, 0.92, 0.55):
        draw.ellipse((cx - r * k, cy - r * k, cx + r * k, cy + r * k), outline=blue, width=4)
    for i in range(10):
        a = 2 * math.pi * i / 10
        tip = (cx + r * 0.9 * math.cos(a), cy + r * 0.9 * math.sin(a))
        draw.line(((cx + r * 0.55 * math.cos(a - 0.3), cy + r * 0.55 * math.sin(a - 0.3)), tip,
                   (cx + r * 0.55 * math.cos(a + 0.3), cy + r * 0.55 * math.sin(a + 0.3))), fill=blue, width=4)
    if answer:
        draw.text((cx - 50, cy - 45), '99', fill=(25, 40, 130), font=ImageFont.truetype(str(HAND), 70))


def page(*, printed_name=False, filled_name=False, blue_answer=False, coloured=False, blank=False, mandala=None):
    image = Image.new('RGB', (1240, 1754), 'white')
    draw = ImageDraw.Draw(image)
    if blank:
        return image
    draw.text((90, 90), 'Unidad 3 · El sustantivo', fill=(0, 104, 95) if coloured else 'black', font=ImageFont.truetype(str(BOLD), 44))
    y = 180
    if printed_name:
        draw.text((90, y), f'Estudiante: {NAME}', fill='black', font=ImageFont.truetype(str(PRINT), 30))
        y += 60
    if filled_name:
        draw.text((90, y), 'Nombre:', fill='black', font=ImageFont.truetype(str(PRINT), 30))
        draw.line((230, y + 36, 700, y + 36), fill='black', width=2)
        draw.text((250, y - 8), 'Ximena Quilumba', fill=(25, 40, 130), font=ImageFont.truetype(str(HAND), 42))
        y += 70
    for line in EXERCISE:
        draw.text((90, y), line, fill='black', font=ImageFont.truetype(str(PRINT), 30))
        y += 56
    if blue_answer:
        draw.text((200, y), 'mesa, casa y lápiz', fill=(25, 40, 130), font=ImageFont.truetype(str(HAND), 38))
        y += 60
    if mandala is not None:
        blue_drawing(draw, 620, y + 330, 280, answer=mandala == 'answer')
    if coloured:  # printed colour that must not be taken for handwriting: a leaf, an orange frame, a red box
        draw.ellipse((300, y + 20, 420, y + 80), fill=(80, 170, 70))
        draw.rectangle((500, y + 10, 760, y + 120), outline=(230, 120, 40), width=8)
        draw.rounded_rectangle((820, y + 20, 920, y + 90), radius=10, fill=(200, 40, 40))
    return image


def jpeg(image):
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=90)
    return buffer.getvalue()


def scanned_pdf(image):
    buffer = io.BytesIO()
    image.save(buffer, 'PDF', resolution=150)
    return buffer.getvalue()


@unittest.skipUnless(READY, 'needs the local Tesseract reader and the test fonts')
class PhotoCheckTests(TestCase):
    def test_a_clean_page_is_allowed_as_it_is(self):
        result = photo_check.check_image(page(), [NAME])
        self.assertTrue(result.allowed, result.reasons)
        self.assertEqual(result.preview(), '')
        self.assertEqual(result.image.size, (1240, 1754))

    def test_printed_colour_is_not_taken_for_handwriting(self):
        self.assertTrue(photo_check.check_image(page(coloured=True), [NAME]).allowed)

    def test_a_blue_line_drawing_is_not_taken_for_handwriting(self):
        self.assertTrue(photo_check.check_image(page(mandala='clean'), [NAME]).allowed)

    def test_writing_inside_a_blue_drawing_is_still_found(self):
        result = photo_check.check_image(page(mandala='answer'), [NAME])
        self.assertFalse(result.allowed)
        self.assertIn('escritura a mano', result.message)

    def test_a_registered_name_is_refused_and_shown(self):
        result = photo_check.check_image(page(printed_name=True), [NAME])
        self.assertFalse(result.allowed)
        self.assertIn('nombres', result.message)
        self.assertIn(photo_check.ASK_CLEAN_PAGE, result.message)
        self.assertTrue(result.preview().startswith('data:image/jpeg;base64,'))
        self.assertNotIn('Ximena', result.message)  # the message never repeats the data

    def test_a_filled_name_field_is_refused(self):
        result = photo_check.check_image(page(filled_name=True), [])  # not even a registered name is needed
        self.assertFalse(result.allowed)
        self.assertIn('campos de datos personales llenos', result.message)

    def test_blue_pen_handwriting_is_refused(self):
        result = photo_check.check_image(page(blue_answer=True), [])
        self.assertFalse(result.allowed)
        self.assertIn('escritura a mano', result.message)

    def test_a_page_that_cannot_be_read_is_refused(self):
        result = photo_check.check_image(page(blank=True), [])
        self.assertEqual((result.allowed, result.reasons), (False, [photo_check.UNREADABLE]))

    def test_without_the_local_reader_nothing_is_sent(self):
        with mock.patch('apps.students.documents.tesseract', return_value=None):
            result = photo_check.check_image(page(), [])
        self.assertEqual((result.allowed, result.reasons), (False, [photo_check.UNCHECKABLE]))

    def test_any_error_refuses(self):
        with mock.patch.object(photo_check, '_ocr', side_effect=OSError('fallo')):
            self.assertFalse(photo_check.check_image(page(), []).allowed)

    def test_the_document_check_uses_it_for_photos_and_scanned_pdfs(self):
        clean = document_privacy.inspect_file(jpeg(page()), [NAME])
        self.assertEqual((clean.status, len(clean.images)), (document_privacy.APTO, 1))
        refused = document_privacy.inspect_file(jpeg(page(printed_name=True)), [NAME])
        self.assertEqual(refused.status, document_privacy.BLOQUEADO)
        self.assertTrue(refused.preview)
        scan = document_privacy.inspect_file(scanned_pdf(page()), [NAME])
        self.assertEqual((scan.status, len(scan.images), scan.text), (document_privacy.APTO, 1, ''))
        scan = document_privacy.inspect_file(scanned_pdf(page(filled_name=True)), [NAME])
        self.assertEqual(scan.status, document_privacy.BLOQUEADO)
        self.assertIn('Página 1 del PDF escaneado', scan.message)


@unittest.skipUnless(READY, 'needs the local Tesseract reader and the test fonts')
@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class PhotoUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.fotos@maestrosombra.test', None, full_name='Docente Fotos',
                                                  accepted_terms=True, terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name=NAME, birth_date=date(2016, 1, 1), grade='EGB_5',
                              conditions=['DYSLEXIA'], diagnosis_source='IN_EVALUATION')
        cls.student.save()
        cls.language = Subject.objects.get(code='LEN')

    def form(self, image):
        return AdaptMaterialForm({'students': [str(self.student.pk)], 'subject': self.language.pk,
                                  'topic': 'El sustantivo', 'input_type': 'PHOTO', f'rules_{self.student.pk}': []},
                                 {'file': SimpleUploadedFile('pagina.jpg', jpeg(image), content_type='image/jpeg')},
                                 teacher=self.teacher)

    def test_the_form_offers_photos_and_accepts_a_clean_one(self):
        form = self.form(page())
        self.assertIn('PHOTO', [value for value, _label in form.fields['input_type'].choices])
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_form_refuses_a_photo_with_data_and_shows_what_was_found(self):
        before = OriginalMaterial.objects.count()
        form = self.form(page(filled_name=True))
        self.assertFalse(form.is_valid())
        self.assertIn('Por privacidad no se envía a la IA', form.errors['file'][0])
        self.assertTrue(form.privacy_previews['file'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(OriginalMaterial.objects.count(), before)  # nothing stored

    def adapt(self, image):
        material = OriginalMaterial(teacher=self.teacher, subject=self.language, title='Sustantivos',
                                    topic='El sustantivo', input_type='PHOTO')
        material.file.save('pagina.jpg', ContentFile(jpeg(image)), save=False)
        material.save()
        fake, images = FakeGemini(), []

        def recording(system, parts, schema):
            images.extend(p for p in parts if 'inlineData' in p)
            return fake(system, parts, schema)
        with mock.patch.object(ai, 'generate_json', recording):
            services.process_material(material.pk, [(self.student.pk, ['DYSLEXIA'])], {self.student.pk: []})
        material.refresh_from_db()
        from django.core.files.storage import default_storage
        default_storage.delete(material.file.name)
        return material, images, fake

    def test_a_clean_photo_is_sent_and_adapted(self):
        material, images, fake = self.adapt(page())
        self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
        self.assertEqual(len(images), 1)  # the photo, once, as it is
        self.assertNotIn(NAME, fake.everything())

    def test_a_photo_with_data_never_reaches_gemini(self):
        material, images, fake = self.adapt(page(printed_name=True))
        self.assertEqual(material.status, MaterialStatus.ERROR)
        self.assertTrue(material.ai_error.startswith(ai.PRIVACY_BLOCK))
        self.assertEqual((images, fake.requests), ([], []))
