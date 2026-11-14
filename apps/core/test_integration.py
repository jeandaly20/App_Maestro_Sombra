"""
Task 5: end-to-end integration tests through the real views (scenarios A to D), the 23 NEE combinations and the
failure and recovery cases. Gemini is simulated (at the client or at the HTTP level); made-up data only.
Run in the isolated environment: python manage.py test apps --settings=config.settings_test --keepdb
"""
import io
import json
import urllib.error
import zipfile
from contextlib import contextmanager
from datetime import date
from unittest import mock

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from apps.accounts.models import Teacher
from apps.adaptations import ai, pedagogical_relevance
from apps.adaptations.models import AdaptationStatus, CurricularAdaptation, LessonAdaptation, LessonPlan
from apps.adaptations.tests import FakeGemini, SUPPORTED, lesson_answer, worksheet_answer
from apps.core.choices import Condition
from apps.evaluations.models import Evaluation, EvaluationStatus
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student, StudentConsent, Treatment
from apps.students.tests import NOT_SUPPORTED, student_data

NOUNS = ('Lee: "El perro de Ana juega en el parque." 1. Subraya los sustantivos. 2. Escribe tres sustantivos '
         'comunes. 3. Clasifica: perro, Quito, mesa. 4. Escribe una oración. 5. Explica qué es un sustantivo.')
PROFILE = {'learning_strengths': 'Comprende bien las explicaciones orales.',
           'learning_barriers': 'Le cuesta mantener la atención en actividades largas.',
           'pedagogical_supports': 'Pausas programadas e instrucciones breves.'}
SECRET = 'Observación privada: hoy llegó muy cansado porque su familia se mudó.'


class Now:
    """Runs the background job at once, in the test's own transaction."""

    def __init__(self, target, daemon=None, **kwargs):
        self.target = target

    def start(self):
        self.target()


@contextmanager
def synchronous(fake=None):
    fake = fake or FakeGemini()
    with mock.patch('apps.adaptations.services.threading.Thread', Now), \
            mock.patch('apps.adaptations.services.close_old_connections'), \
            mock.patch.object(ai, 'generate_json', fake):
        yield fake


def pdf_bytes(lines):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    for n, line in enumerate(lines):
        pdf.drawString(60, 790 - n * 20, line)
    pdf.save()
    return buffer.getvalue()


def scanned_pdf_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (800, 1100), 'white').save(buffer, 'PDF')
    return buffer.getvalue()


def plan_bytes():
    """A made-up weekly plan: one sheet per week, three sessions with title, objective, skill and activities."""
    book = Workbook()
    sheet = book.active
    sheet.title = 'Semana 1'
    rows = [['PLANIFICACIÓN SEMANAL', 'Ciencias Naturales', '4.º EGB']]
    for number, title in ((1, 'Las partes de la planta'), (2, 'La raíz y el tallo'), (3, 'Las hojas y la flor')):
        rows += [[f'Sesión {number}', 'Título:', title],
                 ['Objetivo:', f'Identificar {title.lower()} y su función.'],
                 ['Destreza:', 'Observar y describir las partes de las plantas.'],
                 ['Actividades:', 'Anticipación: observar una planta. Construcción: dibujar y rotular. '
                                  'Consolidación: completar un organizador.'],
                 ['Evaluación:', 'Rotula correctamente las partes de la planta.'], []]
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def flashes(response):
    return ' '.join(str(m) for m in get_messages(response.wsgi_request))


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class IntegrationBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.integral@maestrosombra.test', None,
                                                  full_name='Docente Integral', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.language, cls.science = Subject.objects.get(code='LEN'), Subject.objects.get(code='CIE')

    def setUp(self):
        self.client.force_login(self.teacher)
        ai._paused_until.clear()

    def tearDown(self):
        from django.core.files.storage import default_storage
        for material in OriginalMaterial.objects.filter(teacher=self.teacher):
            if material.file:
                default_storage.delete(material.file.name)
        for lesson in LessonPlan.objects.filter(teacher=self.teacher):
            for field in (lesson.plan_file, lesson.guide_file):
                if field:
                    default_storage.delete(field.name)
        for adaptation in CurricularAdaptation.objects.filter(material__teacher=self.teacher):
            if adaptation.pdf:
                default_storage.delete(adaptation.pdf.name)

    def created(self, queryset, action):
        """
        The record the action created, found by exclusion. Not by created_at: on Windows the clock moves in steps
        of 15.6 ms, so two records made one after the other can share the same time (Task 5.1).
        """
        before = set(queryset.values_list('pk', flat=True))
        result = action()
        return result, queryset.exclude(pk__in=before).get()

    def register(self, conditions=('DYSLEXIA', 'ADHD'), **extra):
        response, student = self.created(Student.objects.filter(teacher=self.teacher), lambda: self.client.post(
            reverse('student_create'), student_data(list(conditions), **{**PROFILE, **extra})))
        self.assertEqual(response.status_code, 302, response.content[:300])
        return student

    def adapt_text(self, student, text=NOUNS, fake=None):
        with synchronous(fake) as used:
            response, material = self.created(OriginalMaterial.objects.filter(teacher=self.teacher), lambda: self.client.post(
                reverse('adapt'), {'students': [str(student.pk)], 'subject': self.language.pk, 'topic': 'El sustantivo',
                                   'input_type': 'TEXT', 'input_text': text, f'rules_{student.pk}': ['SEGMENTATION']}))
        return response, material, used


class ScenarioTests(IntegrationBase):
    def test_A_register_adapt_review_pdf_and_bank(self):
        student = self.register(('DYSLEXIA', 'ADHD'))
        self.assertEqual(student.conditions, ['DYSLEXIA', 'ADHD'])
        self.assertEqual(student.learning_barriers, PROFILE['learning_barriers'])
        response, material, fake = self.adapt_text(student)
        self.assertRedirects(response, reverse('material_detail', args=[material.pk]))
        self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
        adaptation = material.adaptations.get()
        self.assertEqual(adaptation.conditions, ['DYSLEXIA', 'ADHD'])
        self.assertIn('relevance', adaptation.content)
        self.assertTrue(adaptation.content['review_checks']['teacher_review_required'])
        page = self.client.get(reverse('material_detail', args=[material.pk])).content.decode()
        self.assertIn('Pertinencia pedagógica y comprobaciones', page)
        # The PDF only after approval
        self.assertEqual(self.client.get(reverse('adaptation_pdf', args=[adaptation.pk])).status_code, 302)
        self.client.post(reverse('adaptation_action', args=[adaptation.pk]), {'action': 'approve'})
        pdf = self.client.get(reverse('adaptation_pdf', args=[adaptation.pk]))
        self.assertEqual((pdf.status_code, pdf['Content-Type']), (200, 'application/pdf'))
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertContains(self.client.get(reverse('bank')), adaptation.code)
        self.assertNotIn(student.name, fake.everything())

    def test_B_whole_class_from_a_weekly_plan(self):
        student = self.register(('ADHD',))
        upload = self.client.post(reverse('lesson_upload'), {
            'plan_file': SimpleUploadedFile('planificacion.xlsx', plan_bytes())})
        lesson = LessonPlan.objects.get(teacher=self.teacher)
        self.assertRedirects(upload, reverse('lesson_setup', args=[lesson.pk]))
        setup = self.client.get(reverse('lesson_setup', args=[lesson.pk]))
        session = setup.context['form'].fields['session'].choices[1][0]
        book = pdf_bytes(['LAS PARTES DE LA PLANTA', 'La planta tiene raíz, tallo, hojas y flor.',
                          '1. Observa la planta. 2. Dibuja sus partes. 3. Rotula la raíz y el tallo.'])
        with synchronous(), mock.patch('apps.adaptations.services.document_privacy.inspect_file',
                                       wraps=__import__('apps.adaptations.document_privacy',
                                                        fromlist=['x']).inspect_file):
            response = self.client.post(reverse('lesson_setup', args=[lesson.pk]), {
                'subject': self.science.pk, 'session': session, 'students': [str(student.pk)],
                f'rules_{student.pk}': ['STEP_SEQUENCE'], 'adaptation_grade': '',
                'book_files': SimpleUploadedFile('libro.pdf', book, content_type='application/pdf')})
        lesson.refresh_from_db()
        self.assertRedirects(response, reverse('lesson_detail', args=[lesson.pk]))
        self.assertEqual(lesson.status, MaterialStatus.PROCESSED, lesson.ai_error)
        self.assertIn('Sesión 1', lesson.session_text)
        adapted = lesson.adaptations.get()
        moments = adapted.content['moments']
        self.assertTrue(moments and all(m.get('name') and m.get('duration') for m in moments))
        self.assertTrue(adapted.content.get('adapted_objective'))
        # Review, approve the class and its worksheet, then download everything
        saved = self.client.post(reverse('lesson_save', args=[adapted.pk]),
                                 {'action': 'approve', 'plan_row': 'Segmentar las consignas.'})
        self.assertEqual(saved.status_code, 302)
        self.client.post(reverse('adaptation_action', args=[adapted.worksheet.pk]), {'action': 'approve'})
        excel = self.client.get(reverse('lesson_excel', args=[adapted.pk]))
        self.assertEqual(excel.status_code, 200)
        self.assertTrue(excel.content.startswith(b'PK'))  # an .xlsx, like the uploaded plan
        archive = self.client.get(reverse('lesson_zip', args=[lesson.pk]))
        self.assertEqual(archive.status_code, 200)
        names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
        self.assertTrue(any(name.endswith('.pdf') for name in names) and any(name.endswith('.xlsx') for name in names))

    def test_C_evaluate_follow_up_review_and_regenerate(self):
        student = self.register(('DYSLEXIA',))
        _response, material, _fake = self.adapt_text(student)
        first = material.adaptations.get()
        with synchronous() as fake:
            response = self.client.post(reverse('evaluation', args=[first.pk]), {
                'action': 'final', 'goal_attainment': 'LESS', 'autonomy_level': 'FREQUENT_SUPPORT',
                'comprehension': 'DIFFICULTY', 'overload': 'MILD', 'observation': SECRET,
                'supports_applied': ['pausas', 'instrucciones_breves'], 'supports_useful': ['pausas'],
                'regeneration_requested': 'on'})
        self.assertEqual(response.status_code, 302)
        evaluation = Evaluation.objects.get(adaptation=first)
        self.assertEqual((evaluation.status, evaluation.recommendation_source), (EvaluationStatus.FINAL, 'LOCAL'))
        self.assertTrue(any('Pausas programadas' in item['text'] for item in evaluation.local_recommendations))
        self.assertNotIn('cansado', fake.everything())
        # The new version exists and the previous one is kept with its evaluation
        versions = list(material.adaptations.order_by('version'))
        self.assertEqual([v.version for v in versions], [1, 2])
        self.assertEqual(versions[1].source_adaptation, first)
        self.assertTrue(Evaluation.objects.filter(adaptation=first).exists())
        progress = self.client.get(reverse('student_progress', args=[student.pk]))
        self.assertContains(progress, 'Historial de aprendizaje')
        self.assertContains(progress, 'progress-chart__dot')  # the registered value is drawn
        rec = progress.context['recommendations'][0]
        self.client.post(reverse('student_recommendation_review', args=[student.pk]),
                         {'key': rec['key'], 'decision': 'CONSIDERED'})
        again = self.client.get(reverse('student_progress', args=[student.pk])).context['recommendations'][0]
        self.assertFalse(again['pending'])
        self.client.post(reverse('evaluation_recommendation_review', args=[first.pk]))
        evaluation.refresh_from_db()
        self.assertIsNotNone(evaluation.recommendation_reviewed_at)

    def test_D_privacy_end_to_end(self):
        student = self.register(('ADHD',), name='Ximena Lorena Tapia')
        # 1-2. A scanned PDF and a photo are refused before anything is saved or sent
        for name, data, kind in (('escaneo.pdf', scanned_pdf_bytes(), 'PDF'), ('foto.jpg', b'\xff\xd8\xff' + b'0' * 40, 'PHOTO')):
            before = OriginalMaterial.objects.count()
            with synchronous() as fake:
                response = self.client.post(reverse('adapt'), {
                    'students': [str(student.pk)], 'subject': self.language.pk, 'topic': 'El sustantivo',
                    'input_type': kind, 'file': SimpleUploadedFile(name, data)})
            self.assertEqual(response.status_code, 200)  # the form again, with the reason
            self.assertEqual((OriginalMaterial.objects.count(), fake.requests), (before, []))
        # 3-4. A digital PDF with made-up identifiers: only the cleaned version leaves
        identified = pdf_bytes(['Nombre: Ximena Lorena Tapia', 'Cédula: 0923456781', 'Correo: ximena@correo.com',
                                'GUÍA: EL SUSTANTIVO', '1. Subraya los sustantivos.', '2. Escribe tres sustantivos.'])
        with synchronous() as fake:
            _response, material = self.created(OriginalMaterial.objects.filter(teacher=self.teacher), lambda: self.client.post(
                reverse('adapt'), {'students': [str(student.pk)], 'subject': self.language.pk, 'topic': 'El sustantivo',
                                   'input_type': 'PDF',
                                   'file': SimpleUploadedFile('guia.pdf', identified, content_type='application/pdf')}))
        self.assertEqual(material.ai_analysis['privacy']['status'], 'DEPURADO')
        for private in ('Ximena', 'Tapia', '0923456781', 'ximena@correo.com'):
            self.assertNotIn(private, fake.everything())
        # 5-6. Without, and with a revoked, authorization the evaluation stays local
        adaptation = material.adaptations.get()
        with self.settings(AI_EVALUATION_TRANSFER_ENABLED=True), synchronous() as fake:
            consent = StudentConsent.objects.create(student=student, document_version='IA-EVAL', authorized_on=date(2026, 11, 1),
                                                    treatments=[Treatment.INTERNAL, Treatment.AI_EVALUATION],
                                                    status='REVOKED', revoked_at=timezone.now())
            self.client.post(reverse('evaluation', args=[adaptation.pk]), {
                'action': 'final', 'goal_attainment': 'EXPECTED', 'autonomy_level': 'AUTONOMOUS',
                'comprehension': 'YES', 'observation': SECRET})
        self.assertEqual(Evaluation.objects.get(adaptation=adaptation).recommendation_source, 'LOCAL')
        self.assertFalse(any('recommendation' in r['schema']['properties'] for r in fake.requests))
        self.assertNotIn('cansado', fake.everything())  # 8. the observation never leaves
        consent.delete()
        # 7. Another teacher sees nothing of this student
        intruder = Teacher.objects.create_user('otro.integral@maestrosombra.test', None, full_name='Otro',
                                               accepted_terms=True, terms_accepted_at=timezone.now())
        self.client.force_login(intruder)
        for url in (reverse('student_edit', args=[student.pk]), reverse('student_progress', args=[student.pk]),
                    reverse('material_detail', args=[material.pk]), reverse('evaluation', args=[adaptation.pk]),
                    reverse('student_file', args=[student.pk])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertNotContains(self.client.get(reverse('bank')), adaptation.code)


class CombinationTests(IntegrationBase):
    def test_the_23_combinations_register_adapt_and_keep_their_schema(self):
        self.assertEqual(len(SUPPORTED), 23)
        schema_keys = set(ai.ADAPTATION_SCHEMA['required'])
        for conditions in SUPPORTED:
            with self.subTest(conditions=conditions):
                student = self.register(conditions, name=f'Estudiante {"".join(c[:2] for c in conditions)}')
                self.assertEqual(student.conditions, conditions)
                relevance = pedagogical_relevance.analyze(
                    conditions=conditions, profile=PROFILE, subject_code='LEN', subject_name='Lengua y Literatura',
                    topic='El sustantivo', activity_text=NOUNS, exercise_count=5)
                self.assertEqual({item['condition'] for item in relevance['nee']}, set(conditions))  # none invented
                _r, material, fake = self.adapt_text(student)
                self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
                adaptation = material.adaptations.get()
                self.assertEqual(adaptation.conditions, conditions)
                self.assertTrue(schema_keys <= set(adaptation.content))
                sent = fake.everything()
                registered = {w for c in conditions for w in pedagogical_relevance.DIAGNOSIS_WORDS[Condition(c)]}
                for condition in set(Condition) - {Condition(c) for c in conditions}:
                    for word in set(pedagogical_relevance.DIAGNOSIS_WORDS[condition]) - registered:
                        self.assertFalse(pedagogical_relevance.mentions(adaptation.content.get('summary', '').lower(), word))
                self.assertNotIn(student.name, sent)
                student.delete()

    def test_the_excluded_combinations_are_rejected(self):
        self.assertEqual(len(NOT_SUPPORTED), 63 - 23)
        for conditions in NOT_SUPPORTED:
            with self.subTest(conditions=conditions):
                response = self.client.post(reverse('student_create'), student_data(conditions))
                self.assertEqual(response.status_code, 200)
        self.assertFalse(Student.objects.filter(teacher=self.teacher).exists())


@override_settings(GEMINI_API_KEY='clave-de-prueba', AI_EVALUATION_TRANSFER_ENABLED=True)
class FailureTests(IntegrationBase):
    """Failures of the provider and of local steps: nothing is lost, the message is clear, recovery works."""

    def http_failure(self, error):
        def call(*args, **kwargs):
            raise error
        return call

    def adapt_with_call(self, call):
        student = Student.objects.filter(teacher=self.teacher).first() or self.register(('ADHD',))
        with mock.patch('apps.adaptations.services.threading.Thread', Now), \
                mock.patch('apps.adaptations.services.close_old_connections'), \
                mock.patch.object(ai, '_call', call), mock.patch.object(ai.time, 'sleep'):
            _response, material = self.created(OriginalMaterial.objects.filter(teacher=self.teacher), lambda: self.client.post(
                reverse('adapt'), {'students': [str(student.pk)], 'subject': self.language.pk, 'topic': 'El sustantivo',
                                   'input_type': 'TEXT', 'input_text': NOUNS}))
        return material

    def test_gemini_unavailable_timeout_and_no_connection(self):
        cases = [
            (urllib.error.HTTPError('u', 503, 'Service Unavailable', {}, None), ai.OVERLOADED),
            (TimeoutError(), ai.OVERLOADED),
            (urllib.error.URLError('sin red'), ai.NO_CONNECTION),
        ]
        for error, message in cases:
            with self.subTest(error=type(error).__name__):
                ai._paused_until.clear()
                material = self.adapt_with_call(self.http_failure(error))
                self.assertEqual((material.status, material.ai_error), (MaterialStatus.ERROR, message))
                page = self.client.get(reverse('material_detail', args=[material.pk])).content.decode()
                self.assertIn('No se pudo procesar el material', page)
                self.assertIn('Reintentar', page)  # what was done is kept; it continues from the failed step

    def test_invalid_and_incomplete_answers(self):
        invalid = {'candidates': [{'content': {'parts': [{'text': 'esto no es JSON'}]}}]}
        material = self.adapt_with_call(lambda *a, **k: invalid)
        self.assertEqual(material.status, MaterialStatus.ERROR)
        self.assertIn('no devolvió una respuesta válida', material.ai_error)
        # A JSON answer without the fields the worksheet needs
        fake = FakeGemini()

        def incomplete(system, parts, schema):
            if 'concepts' in schema['properties']:
                return {'title': 'Ficha'}, 'fake'
            return fake(system, parts, schema)
        student = self.register(('ADHD',))
        _response, material, _used = self.adapt_text(student, fake=incomplete)
        self.assertIn(material.status, (MaterialStatus.PROCESSED, MaterialStatus.ERROR))
        if material.status == MaterialStatus.ERROR:
            self.assertTrue(material.ai_error)

    def test_extraction_failure_blocks_the_document(self):
        student = self.register(('ADHD',))
        before = OriginalMaterial.objects.count()
        response = self.client.post(reverse('adapt'), {
            'students': [str(student.pk)], 'subject': self.language.pk, 'topic': 'El sustantivo', 'input_type': 'PDF',
            'file': SimpleUploadedFile('roto.pdf', b'%PDF-1.4 esto no es un PDF', content_type='application/pdf')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OriginalMaterial.objects.count(), before)
        self.assertTrue(response.context['form'].errors.get('file'))

    def test_pdf_generation_error(self):
        student = self.register(('DYSLEXIA',))
        _response, material, _fake = self.adapt_text(student)
        adaptation = material.adaptations.get()
        self.client.post(reverse('adaptation_action', args=[adaptation.pk]), {'action': 'approve'})
        with mock.patch('apps.adaptations.views.worksheet_pdf', return_value=None):
            response = self.client.get(reverse('adaptation_pdf', args=[adaptation.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('No se pudo generar el PDF', flashes(response))

    def test_class_interrupted_and_resumed(self):
        first, second = self.register(('ADHD',), name='Primer Estudiante'), self.register(('DYSLEXIA',), name='Segundo Estudiante')
        book = OriginalMaterial.objects.create(teacher=self.teacher, subject=self.science, title='Plantas',
                                               topic='Semana 1', input_type='TEXT', input_text='La planta tiene raíz.')
        lesson = LessonPlan.objects.create(teacher=self.teacher, subject=self.science, material=book,
                                           plan_file=SimpleUploadedFile('plan.xlsx', plan_bytes()),
                                           session_label='Sesión 1', title='Las partes de la planta',
                                           session_text='Sesión 1. Las partes de la planta. Objetivo: identificar.')
        fake, calls = FakeGemini(), {'lesson': 0}

        def flaky(system, parts, schema):
            if 'moments' in schema['properties'] and 'plan_row' in schema['properties']:
                calls['lesson'] += 1
                if calls['lesson'] == 2:
                    raise ai.AIError(ai.OVERLOADED)
            return fake(system, parts, schema)
        from apps.adaptations import services
        requests = [(first.pk, first.conditions), (second.pk, second.conditions)]
        with mock.patch.object(ai, 'generate_json', flaky):
            services.process_lesson(lesson.pk, requests, {}, check_topic=False)
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, MaterialStatus.ERROR)
        self.assertEqual(lesson.adaptations.count(), 1)  # the first student's class is kept
        with mock.patch.object(ai, 'generate_json', flaky):
            services.process_lesson(lesson.pk, requests, {}, check_topic=False, resume=True)
        lesson.refresh_from_db()
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.PROCESSED, 2))

    def test_failed_regeneration_keeps_everything(self):
        student = self.register(('DYSLEXIA',))
        _response, material, _fake = self.adapt_text(student)
        adaptation = material.adaptations.get()
        evaluation = Evaluation.objects.create(adaptation=adaptation, status=EvaluationStatus.FINAL,
                                               autonomy_level='FREQUENT_SUPPORT', comprehension='NO', observation=SECRET)
        before = (adaptation.content, evaluation.comprehension)

        def down(*args, **kwargs):
            raise ai.AIError(ai.OVERLOADED)
        with mock.patch.object(ai, 'generate_json', down):
            response = self.client.post(reverse('adaptation_action', args=[adaptation.pk]), {'action': 'regenerate'})
        self.assertIn('saturados', flashes(response))
        adaptation.refresh_from_db()
        evaluation.refresh_from_db()
        self.assertEqual((adaptation.content, evaluation.comprehension), before)
        self.assertEqual((material.adaptations.count(), evaluation.observation), (1, SECRET))

    def test_errors_never_leak_private_data(self):
        student = self.register(('ADHD',), name='Zulema Andrea Quiroz')
        with self.assertLogs('maestro_sombra', level='INFO') as logs:
            material = self.adapt_with_call(self.http_failure(urllib.error.HTTPError('u', 503, 'x', {}, None)))
        output = '\n'.join(logs.output) + material.ai_error
        for private in ('Zulema', 'Quiroz', 'sustantivos'):
            self.assertNotIn(private, output)
        self.assertNotIn('Zulema', json.dumps(material.job or {}))
        self.assertTrue(student.pk)
