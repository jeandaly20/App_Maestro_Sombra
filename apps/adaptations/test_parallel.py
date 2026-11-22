"""
Speed of the AI without losing safety: the students of one material or class are adapted at the same time, and
each call to Gemini has a time limit sized to its work. Gemini is simulated; made-up data only.
"""
import threading
import time
from datetime import date
from unittest import mock

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.adaptations import ai, lesson_plan, services
from apps.adaptations.models import CurricularAdaptation, LessonAdaptation, LessonPlan
from apps.adaptations.tests import FakeGemini
from apps.core.test_integration import plan_bytes
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student

IMAGE = {'inlineData': {'mimeType': 'image/jpeg', 'data': ''}}


@override_settings(GEMINI_TIMEOUT_SECONDS=30, GEMINI_TIMEOUT_PER_IMAGE_SECONDS=5, GEMINI_MAX_TIMEOUT_SECONDS=60,
                   GEMINI_API_KEY='clave-de-prueba', GEMINI_MODEL='modelo-a',
                   GEMINI_FALLBACK_MODELS=['modelo-b', 'modelo-c'])
class TimeLimitTests(SimpleTestCase):
    def setUp(self):
        ai._paused_until.clear()

    def test_the_limit_grows_with_the_pages_up_to_the_maximum(self):
        self.assertEqual(ai.timeout_for([{'text': 'una ficha'}]), 30)
        self.assertEqual(ai.timeout_for([{'text': 'una página'}, IMAGE]), 30)
        self.assertEqual(ai.timeout_for([IMAGE] * 4), 45)
        self.assertEqual(ai.timeout_for([IMAGE] * 20), 60)

    def test_the_last_model_always_gets_the_maximum(self):
        self.assertEqual(ai.timeout_for([{'text': 'una ficha'}], last_model=True), 60)

    def test_a_stuck_model_is_left_after_its_limit_and_the_last_one_waits_the_most(self):
        limits = []

        def call(model, system, parts, schema, timeout):
            limits.append((model, timeout))
            if model != 'modelo-c':
                raise TimeoutError()
            return {'candidates': [{'content': {'parts': [{'text': '{"ok": true}'}]}}], 'modelVersion': model}

        with mock.patch.object(ai, '_call', call):
            data, model = ai.generate_json('sistema', [{'text': 'una ficha'}], {'type': 'object'})
        self.assertEqual((data, model), ({'ok': True}, 'modelo-c'))
        # One try each on a timeout (retrying would only add waiting): 30 s, 30 s and the maximum for the last
        self.assertEqual(limits, [('modelo-a', 30), ('modelo-b', 30), ('modelo-c', 60)])


class ForEachStudentTests(SimpleTestCase):
    """The helper alone, without the database."""

    @override_settings(AI_PARALLEL_STUDENTS=3)
    def test_three_students_run_at_the_same_time(self):
        barrier = threading.Barrier(3, timeout=5)  # only passes if the three are running together
        messages, seen = [], []
        services.for_each_student([1, 2, 3], lambda item: (barrier.wait(), seen.append(item)), messages.append, 'ficha')
        self.assertEqual(sorted(seen), [1, 2, 3])
        self.assertEqual(messages[0], 'Adaptando 3 fichas a la vez…')
        self.assertEqual(messages[-1], 'Adaptando 3 fichas a la vez: 3 de 3 listas…')

    @override_settings(AI_PARALLEL_STUDENTS=3)
    def test_one_failure_does_not_stop_the_others_and_is_reported_at_the_end(self):
        seen = []

        def work(item):
            if item == 2:
                raise ai.AIError(ai.OVERLOADED)
            time.sleep(0.05)
            seen.append(item)

        with self.assertRaisesMessage(ai.AIError, ai.OVERLOADED):
            services.for_each_student([1, 2, 3], work, lambda text: None, 'ficha')
        self.assertEqual(sorted(seen), [1, 3])  # saved, so "Reintentar" only asks for the second student

    @override_settings(AI_PARALLEL_STUDENTS=1)
    def test_with_one_worker_it_goes_one_after_another_and_stops_at_the_first_error(self):
        seen, messages = [], []

        def work(item):
            if item == 2:
                raise ai.AIError(ai.OVERLOADED)
            seen.append(item)

        with self.assertRaises(ai.AIError):
            services.for_each_student([1, 2, 3], work, messages.append, 'clase')
        self.assertEqual(seen, [1])
        self.assertEqual(messages, ['Adaptando la clase 1 de 3…', 'Adaptando la clase 2 de 3…'])


class CountingGemini(FakeGemini):
    """The simulated AI; each adaptation call lasts a moment and records how many were running together."""

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.running = self.most_at_once = 0

    def __call__(self, system, parts, schema):
        props = schema['properties']
        if 'concepts' not in props and 'plan_row' not in props:  # reading the material or the plan
            return super().__call__(system, parts, schema)
        with self.lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        try:
            time.sleep(0.3)
            return super().__call__(system, parts, schema)
        finally:
            with self.lock:
                self.running -= 1


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False, AI_PARALLEL_STUDENTS=3)
class ParallelAdaptationTests(SimpleTestCase):
    """
    The real flow with real threads, each with its own PostgreSQL connection. The threads only see committed
    data, so this test commits its own rows and deletes exactly those at the end (as test_resume does).
    """
    databases = {'default'}

    def setUp(self):
        self.teacher = Teacher.objects.create_user('docente.paralelo@maestrosombra.test', None,
                                                   full_name='Docente Paralelo', accepted_terms=True,
                                                   terms_accepted_at=timezone.now())
        self.science = Subject.objects.get(code='CIE')
        self.students = []
        for number, conditions in enumerate([['ADHD'], ['DYSLEXIA'], ['ASD']], start=1):
            student = Student(teacher=self.teacher, name=f'Estudiante Paralelo {number}', birth_date=date(2016, 1, 1),
                              grade='EGB_4', conditions=conditions, diagnosis_source='IN_EVALUATION')
            student.save()
            self.students.append(student)
        self.requests = [(s.pk, list(s.conditions)) for s in self.students]

    def tearDown(self):
        from django.core.files.storage import default_storage
        for lesson in LessonPlan.objects.filter(teacher=self.teacher):
            if lesson.plan_file:
                default_storage.delete(lesson.plan_file.name)
        self.teacher.delete()  # its students, materials, classes and adaptations go with it (cascade)

    def book(self):
        return OriginalMaterial.objects.create(
            teacher=self.teacher, subject=self.science, title='Plantas', topic='Las partes de la planta',
            input_type='TEXT', input_text='La planta tiene raíz, tallo, hojas y flor. 1. Observa. 2. Dibuja.')

    def test_the_worksheets_of_three_students_are_adapted_at_the_same_time(self):
        material, gemini = self.book(), CountingGemini()
        with mock.patch.object(ai, 'generate_json', gemini):
            services.process_material(material.pk, self.requests, {})
        material.refresh_from_db()
        self.assertEqual(material.status, MaterialStatus.PROCESSED, material.ai_error)
        self.assertEqual(sorted(CurricularAdaptation.objects.filter(material=material).values_list('student_id', flat=True)),
                         sorted(s.pk for s in self.students))
        self.assertGreaterEqual(gemini.most_at_once, 2)

    def test_the_classes_of_three_students_are_adapted_at_the_same_time(self):
        plan = plan_bytes()
        week = lesson_plan.weeks(lesson_plan.sessions(plan))[0]
        book = self.book()
        lesson = LessonPlan.objects.create(
            teacher=self.teacher, subject=self.science, material=book, plan_file=ContentFile(plan, name='plan.xlsx'),
            plan_file_name='plan.xlsx', sheet=week['sheet'], session_row=week['row'], session_label='Semana 1',
            title=week['title'], session_text=lesson_plan.session_text(plan, week['sheet'], week['row'],
                                                                       week.get('last_row')))
        gemini = CountingGemini()
        with mock.patch.object(ai, 'generate_json', gemini):
            services.process_lesson(lesson.pk, self.requests, {}, check_topic=False)
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, MaterialStatus.PROCESSED, lesson.ai_error)
        self.assertEqual(LessonAdaptation.objects.filter(lesson=lesson).count(), 3)
        self.assertEqual(CurricularAdaptation.objects.filter(material=book).count(), 3)
        self.assertGreaterEqual(gemini.most_at_once, 2)
