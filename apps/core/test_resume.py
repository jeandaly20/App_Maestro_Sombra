"""
Task 5.1: resuming an interrupted class. What was done is kept, only what is missing is redone, nothing is
duplicated, and two simultaneous retries start a single job. Gemini is simulated; made-up data only.
"""
import hashlib
import threading
from datetime import date, timedelta
from unittest import mock

from django.core.files.base import ContentFile
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.adaptations import ai, lesson_plan, pedagogical_relevance, services
from apps.adaptations.models import CurricularAdaptation, LessonAdaptation, LessonPlan
from apps.adaptations.tests import FakeGemini
from apps.core.test_integration import plan_bytes
from apps.evaluations.models import Evaluation, EvaluationStatus
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student

PROFILE_TEXT = 'Le cuesta mantener la atención en actividades largas.'
SECRET = 'Observación privada: hoy llegó cansado.'


class Gemini(FakeGemini):
    """The simulated AI, counting each kind of request; `fail_on` makes the n-th class adaptation fail."""

    def __init__(self, fail_on=(), error=None):
        super().__init__()
        self.fail_on, self.error = set(fail_on), error or ai.AIError(ai.OVERLOADED)
        self.counts = {'material': 0, 'lesson_read': 0, 'worksheet': 0, 'lesson': 0}

    def __call__(self, system, parts, schema):
        props = schema['properties']
        kind = ('material' if 'extracted_text' in props else 'lesson' if 'plan_row' in props
                else 'lesson_read' if 'skill' in props else 'worksheet')
        self.counts[kind] += 1
        if kind == 'lesson' and self.counts['lesson'] in self.fail_on:
            raise self.error
        return super().__call__(system, parts, schema)


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class ResumeBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.reanudar@maestrosombra.test', None,
                                                  full_name='Docente Reanudar', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.science = Subject.objects.get(code='CIE')

    def students(self, count=2):
        made = []
        for n, conditions in enumerate([['ADHD'], ['DYSLEXIA'], ['ASD']][:count]):
            student = Student(teacher=self.teacher, name=f'Estudiante Ficticio {n + 1}', birth_date=date(2016, 1, 1),
                              grade='EGB_4', conditions=conditions, diagnosis_source='IN_EVALUATION',
                              learning_barriers=PROFILE_TEXT)
            student.save()
            made.append(student)
        return made

    def lesson(self, students, multi_session=True):
        plan = plan_bytes()
        week = lesson_plan.weeks(lesson_plan.sessions(plan))[0]
        book = OriginalMaterial(teacher=self.teacher, subject=self.science, title='Plantas', topic='Semana 1',
                                input_type='TEXT', input_text='La planta tiene raíz, tallo, hojas y flor. 1. Observa. '
                                                              '2. Dibuja. 3. Rotula. 4. Une. 5. Explica.')
        book.save()
        lesson = LessonPlan.objects.create(
            teacher=self.teacher, subject=self.science, material=book, plan_file=ContentFile(plan, name='plan.xlsx'),
            plan_file_name='plan.xlsx', sheet=week['sheet'], session_row=week['row'], session_label='Semana 1',
            title=week['title'], session_text=lesson_plan.session_text(plan, week['sheet'], week['row'], week.get('last_row'))
            if multi_session else 'Sesión 1. Las partes de la planta.')
        requests = [(s.pk, list(s.conditions)) for s in students]
        OriginalMaterial.objects.filter(pk=book.pk).update(job=services.job(requests, {}, check_topic=False))
        return lesson, requests

    def run_lesson(self, lesson, requests, gemini, resume=False):
        with mock.patch.object(ai, 'generate_json', gemini):
            services.process_lesson(lesson.pk, requests, {}, check_topic=False, resume=resume)
        lesson.refresh_from_db()
        return lesson

    def interrupted(self, count=2, fail_on=(2,), **kwargs):
        students = self.students(count)
        lesson, requests = self.lesson(students, **kwargs)
        self.run_lesson(lesson, requests, Gemini(fail_on=fail_on))
        return students, lesson, requests

    def tearDown(self):
        from django.core.files.storage import default_storage
        for material in OriginalMaterial.objects.filter(teacher=self.teacher):
            if material.file:
                default_storage.delete(material.file.name)
        for lesson in LessonPlan.objects.filter(teacher=self.teacher):
            if lesson.plan_file:
                default_storage.delete(lesson.plan_file.name)


class ResumeTests(ResumeBase):
    def test_01_02_the_second_student_fails_and_the_first_is_kept(self):
        (first, second), lesson, _requests = self.interrupted()
        self.assertEqual(lesson.status, MaterialStatus.ERROR)
        self.assertEqual(lesson.ai_error, ai.OVERLOADED)
        self.assertEqual(list(lesson.adaptations.values_list('student_id', flat=True)), [first.pk])
        self.assertTrue(lesson.plan_file)  # kept: it is needed to resume

    def test_03_04_only_the_pending_student_is_retried_without_duplicates(self):
        (first, second), lesson, requests = self.interrupted()
        kept = lesson.adaptations.get(student=first)
        gemini = Gemini()
        self.run_lesson(lesson, requests, gemini, resume=True)
        self.assertEqual(lesson.status, MaterialStatus.PROCESSED)
        self.assertEqual(gemini.counts, {'material': 0, 'lesson_read': 0, 'worksheet': 0, 'lesson': 1})
        self.assertEqual(lesson.adaptations.count(), 2)
        self.assertEqual(lesson.adaptations.get(student=first).pk, kept.pk)  # the same row, not redone
        for student in (first, second):
            self.assertEqual(LessonAdaptation.objects.filter(lesson=lesson, student=student).count(), 1)
            self.assertEqual(CurricularAdaptation.objects.filter(material=lesson.material, student=student).count(), 1)

    def test_05_repeated_retries_are_idempotent(self):
        _students, lesson, requests = self.interrupted()
        self.run_lesson(lesson, requests, Gemini(), resume=True)
        again = Gemini()
        self.run_lesson(lesson, requests, again, resume=True)  # the same job run once more
        self.assertEqual(sum(again.counts.values()), 0)
        self.assertEqual(lesson.adaptations.count(), 2)
        self.assertFalse(services.resume_lesson(lesson))  # finished: nothing to reserve, no new job
        self.client.force_login(self.teacher)
        with mock.patch.object(services, 'start_lesson_processing') as start:
            self.client.post(reverse('lesson_resume', args=[lesson.pk]))
        start.assert_not_called()

    def test_06_error_before_calling_gemini(self):
        students = self.students(2)
        lesson, requests = self.lesson(students)
        real = services.lesson_relevance
        calls = {'n': 0}

        def failing(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('fallo local antes de la IA')
            return real(*args, **kwargs)
        gemini = Gemini()
        with mock.patch.object(services, 'lesson_relevance', failing):
            self.run_lesson(lesson, requests, gemini)
        self.assertEqual((lesson.status, lesson.adaptations.count(), gemini.counts['lesson']), (MaterialStatus.ERROR, 1, 1))
        self.assertIn('error inesperado', lesson.ai_error)
        self.run_lesson(lesson, requests, Gemini(), resume=True)
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.PROCESSED, 2))

    def test_07_error_during_the_gemini_call(self):
        for error in (ai.AIError(ai.NO_CONNECTION), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                LessonPlan.objects.filter(teacher=self.teacher).delete()
                Student.objects.filter(teacher=self.teacher).delete()
                students = self.students(2)
                lesson, requests = self.lesson(students)
                self.run_lesson(lesson, requests, Gemini(fail_on=(2,), error=error))
                self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.ERROR, 1))
                self.run_lesson(lesson, requests, Gemini(), resume=True)
                self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.PROCESSED, 2))

    def test_08_error_after_the_gemini_answer(self):
        students = self.students(2)
        lesson, requests = self.lesson(students)
        real = pedagogical_relevance.review_checks
        calls = {'lesson': 0}

        def failing(content, **kwargs):
            if kwargs.get('kind') == 'lesson':
                calls['lesson'] += 1
                if calls['lesson'] == 2:
                    raise ValueError('fallo al revisar la respuesta')
            return real(content, **kwargs)
        with mock.patch.object(pedagogical_relevance, 'review_checks', failing):
            self.run_lesson(lesson, requests, Gemini())
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.ERROR, 1))  # not saved half-done
        gemini = Gemini()
        self.run_lesson(lesson, requests, gemini, resume=True)
        self.assertEqual((lesson.status, lesson.adaptations.count(), gemini.counts['lesson']), (MaterialStatus.PROCESSED, 2, 1))

    def test_09_error_while_saving(self):
        students = self.students(2)
        lesson, requests = self.lesson(students)
        real = LessonAdaptation.objects.update_or_create
        calls = {'n': 0}

        def failing(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise ConnectionError('la base no respondió al guardar')
            return real(*args, **kwargs)
        with mock.patch.object(LessonAdaptation.objects, 'update_or_create', failing):
            self.run_lesson(lesson, requests, Gemini())
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.ERROR, 1))  # no half row
        self.run_lesson(lesson, requests, Gemini(), resume=True)
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.PROCESSED, 2))

    def test_10_lost_http_response_after_saving(self):
        # The job finished but the teacher never saw the answer and clicks "Reintentar": nothing is redone
        students = self.students(2)
        lesson, requests = self.lesson(students)
        self.run_lesson(lesson, requests, Gemini())
        self.client.force_login(self.teacher)
        with mock.patch.object(services, 'start_lesson_processing') as start:
            self.client.post(reverse('lesson_resume', args=[lesson.pk]))
        start.assert_not_called()
        self.assertEqual(lesson.adaptations.count(), 2)
        # The process died after saving the first student, before marking the class: only the second is done
        LessonPlan.objects.filter(pk=lesson.pk).update(status=MaterialStatus.PROCESSING, plan_file='lessons/x.xlsx',
                                                       updated_at=timezone.now() - timedelta(minutes=20))
        lesson.adaptations.filter(student=students[1]).delete()
        lesson.refresh_from_db()
        gemini = Gemini()
        self.run_lesson(lesson, requests, gemini, resume=True)
        self.assertEqual((lesson.adaptations.count(), gemini.counts['lesson']), (2, 1))

    def test_12_a_class_stuck_in_processing_can_be_recovered(self):
        (first, second), lesson, requests = self.interrupted()
        self.client.force_login(self.teacher)
        # Still running (recent news): it is not touched and no second job starts
        LessonPlan.objects.filter(pk=lesson.pk).update(status=MaterialStatus.PROCESSING, updated_at=timezone.now())
        lesson.refresh_from_db()
        self.assertFalse(services.is_interrupted(lesson))
        self.assertFalse(services.claim_for_retry(lesson))
        # No news for longer than STALE_AFTER (the server stopped): shown as interrupted, and it can be resumed
        LessonPlan.objects.filter(pk=lesson.pk).update(updated_at=timezone.now() - services.STALE_AFTER - timedelta(minutes=1))
        lesson.refresh_from_db()
        self.assertTrue(services.is_interrupted(lesson))
        self.assertEqual(self.client.get(reverse('lesson_status', args=[lesson.pk])).json()['status'], 'ERROR')
        self.assertContains(self.client.get(reverse('lesson_detail', args=[lesson.pk])), 'La adaptación se interrumpió')
        with mock.patch('apps.adaptations.services.threading.Thread') as thread:
            self.client.post(reverse('lesson_resume', args=[lesson.pk]))
        thread.return_value.start.assert_called_once()
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, MaterialStatus.PENDING)
        self.run_lesson(lesson, requests, Gemini(), resume=True)
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.PROCESSED, 2))

    def test_13_14_previous_versions_and_evaluations_are_kept(self):
        (first, second), lesson, requests = self.interrupted()
        v1 = CurricularAdaptation.objects.get(material=lesson.material, student=first)
        evaluation = Evaluation.objects.create(adaptation=v1, status=EvaluationStatus.FINAL, goal_attainment='LESS',
                                               autonomy_level='FREQUENT_SUPPORT', comprehension='DIFFICULTY',
                                               observation=SECRET, supports_applied=['pausas'])
        with mock.patch.object(ai, 'generate_json', Gemini()):
            v2 = services.regenerate(v1)
        before = Evaluation.objects.filter(pk=evaluation.pk).values().get()
        self.run_lesson(lesson, requests, Gemini(), resume=True)
        self.assertEqual(list(CurricularAdaptation.objects.filter(material=lesson.material, student=first)
                              .order_by('version').values_list('pk', flat=True)), [v1.pk, v2.pk])
        self.assertEqual(Evaluation.objects.filter(pk=evaluation.pk).values().get(), before)
        self.assertEqual(lesson.adaptations.get(student=first).worksheet_id, v2.pk)  # still the newest version

    def test_15_files_are_intact(self):
        students = self.students(2)
        lesson, requests = self.lesson(students)
        from django.core.files.storage import default_storage
        book = lesson.material
        book.file.save('libro.pdf', ContentFile(b'%PDF-1.4 libro ficticio'), save=True)
        digest = hashlib.sha256(default_storage.open(book.file.name).read()).hexdigest()
        self.run_lesson(lesson, requests, Gemini(fail_on=(2,)))
        self.assertTrue(default_storage.exists(lesson.plan_file.name))  # kept while the class can be resumed
        self.run_lesson(lesson, requests, Gemini(), resume=True)
        book.refresh_from_db()
        self.assertEqual(hashlib.sha256(default_storage.open(book.file.name).read()).hexdigest(), digest)
        self.assertFalse(lesson.plan_file)  # by design the plan file goes once the class is adapted

    def test_16_resuming_keeps_the_privacy_controls(self):
        (first, second), lesson, requests = self.interrupted()
        Evaluation.objects.create(adaptation=CurricularAdaptation.objects.get(material=lesson.material, student=first),
                                  status=EvaluationStatus.FINAL, autonomy_level='FREQUENT_SUPPORT', comprehension='NO',
                                  observation=SECRET)
        gemini = Gemini()
        self.run_lesson(lesson, requests, gemini, resume=True)
        sent = gemini.everything()
        for private in (first.name, second.name, PROFILE_TEXT, 'cansado', 'Estudiante Ficticio'):
            self.assertNotIn(private, sent)

    def test_17_three_students_failing_twice(self):
        students, lesson, requests = self.interrupted(count=3, fail_on=(2,))
        self.assertEqual(lesson.adaptations.count(), 1)
        self.run_lesson(lesson, requests, Gemini(fail_on=(2,)), resume=True)  # the second works, the third fails
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.ERROR, 2))
        self.run_lesson(lesson, requests, Gemini(), resume=True)
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.PROCESSED, 3))
        self.assertEqual(sorted(lesson.adaptations.values_list('student_id', flat=True)), sorted(s.pk for s in students))

    def test_18_a_plan_with_several_sessions(self):
        (first, second), lesson, requests = self.interrupted(multi_session=True)
        for session in ('Sesión 1', 'Sesión 2', 'Sesión 3'):
            self.assertIn(session, lesson.session_text)
        self.run_lesson(lesson, requests, Gemini(), resume=True)
        self.assertEqual((lesson.status, lesson.adaptations.count()), (MaterialStatus.PROCESSED, 2))
        self.assertTrue(all(a.content.get('moments') for a in lesson.adaptations.all()))


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class ConcurrentRetryTests(SimpleTestCase):
    """
    Real concurrency: several threads, each with its own PostgreSQL connection, ask to retry the same class at
    the same moment (a barrier releases them together). The other threads only see committed data, so this test
    commits its own rows and deletes exactly those at the end. (A TransactionTestCase would empty every table of
    the test database, including the subjects loaded by a migration.)
    """
    databases = {'default'}

    def setUp(self):
        self.teacher = Teacher.objects.create_user('docente.concurrente@maestrosombra.test', None,
                                                   full_name='Docente Concurrente', accepted_terms=True,
                                                   terms_accepted_at=timezone.now())
        science = Subject.objects.get(code='CIE')
        student = Student(teacher=self.teacher, name='Estudiante Concurrente', birth_date=date(2016, 1, 1),
                          grade='EGB_4', conditions=['ADHD'], diagnosis_source='IN_EVALUATION')
        student.save()
        book = OriginalMaterial.objects.create(teacher=self.teacher, subject=science, title='Plantas', topic='Semana 1',
                                               input_type='TEXT', input_text='La planta tiene raíz.',
                                               job=services.job([(student.pk, ['ADHD'])], {}, check_topic=False))
        self.lesson = LessonPlan.objects.create(teacher=self.teacher, subject=science, material=book,
                                                plan_file=ContentFile(plan_bytes(), name='plan.xlsx'),
                                                status=MaterialStatus.ERROR, ai_error=ai.OVERLOADED, title='Plantas')

    def tearDown(self):
        from django.core.files.storage import default_storage
        lesson = LessonPlan.objects.get(pk=self.lesson.pk)
        if lesson.plan_file:
            default_storage.delete(lesson.plan_file.name)
        self.teacher.delete()  # its student, material and class go with it (cascade); nothing else is touched

    def race(self, action, workers=2):
        barrier, results, errors = threading.Barrier(workers), [], []

        def worker():
            try:
                barrier.wait()
                results.append(action())
            except Exception as error:  # noqa: BLE001 - reported by the test
                errors.append(error)
            finally:
                connection.close()  # each thread used its own connection
        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results, errors

    def test_11_two_simultaneous_retries_reserve_the_class_once(self):
        results, errors = self.race(lambda: services.claim_for_retry(LessonPlan.objects.get(pk=self.lesson.pk)))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.status, MaterialStatus.PENDING)

    def test_11b_two_simultaneous_reintentar_start_a_single_job(self):
        started = []
        with mock.patch.object(services, 'start_lesson_processing', lambda *a, **k: started.append(1)):
            results, errors = self.race(lambda: services.resume_lesson(LessonPlan.objects.get(pk=self.lesson.pk)),
                                        workers=4)
        self.assertEqual(errors, [])
        self.assertEqual((sorted(results), len(started)), ([False, False, False, True], 1))
