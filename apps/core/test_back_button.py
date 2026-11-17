"""
"Volver" on every page of the app but Inicio. In the browser it returns to the previous page of the app
(forms.js); its own link is the page's parent, used when there is no previous page. Made-up data only.
"""
import html
import re
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.adaptations.models import AdaptationStatus, CurricularAdaptation, LessonAdaptation, LessonPlan
from apps.materials.models import MaterialStatus, OriginalMaterial, Subject
from apps.students.models import Student

BACK = re.compile(r'<a class="button button--ghost back-link" href="([^"]*)" data-back>')


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class BackButtonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.volver@maestrosombra.test', None, full_name='Docente Volver',
                                                  accepted_terms=True, terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name='Estudiante Ficticio', birth_date=date(2016, 3, 1),
                              grade='EGB_5', conditions=['DYSLEXIA'], diagnosis_source='IN_EVALUATION')
        cls.student.save()
        math = Subject.objects.get(code='MAT')
        cls.material = OriginalMaterial.objects.create(
            teacher=cls.teacher, subject=math, title='Ficha', topic='Multiplicamos', input_type='TEXT',
            input_text='1. Calcula.', status=MaterialStatus.PROCESSED)
        cls.adaptation = CurricularAdaptation.objects.create(
            material=cls.material, student=cls.student, condition='DYSLEXIA', conditions=['DYSLEXIA'], version=1,
            title='Ficha adaptada', content={'learning_goal': 'Vas a aprender.'}, status=AdaptationStatus.APPROVED)
        cls.lesson = LessonPlan.objects.create(teacher=cls.teacher, subject=math, material=cls.material,
                                               plan_file=ContentFile(b'plan', name='plan.xlsx'), title='Multiplicamos',
                                               status=MaterialStatus.PROCESSED)
        LessonAdaptation.objects.create(lesson=cls.lesson, student=cls.student, condition='DYSLEXIA',
                                        conditions=['DYSLEXIA'], grade='NON_SIGNIFICANT', worksheet=cls.adaptation,
                                        content={'moments': [], 'plan_row': []})

    def back(self, url):
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        found = BACK.findall(response.content.decode())
        return [html.unescape(href) for href in found]

    def test_inicio_has_no_back_button(self):
        self.assertEqual(self.back(reverse('home')), [])

    def test_every_other_page_has_one_back_button_to_its_parent(self):
        material = f"{reverse('material_detail', args=[self.material.pk])}?a={self.adaptation.pk}"
        pages = {
            reverse('student_list'): reverse('home'),
            reverse('adapt'): reverse('home'),
            reverse('lesson_upload'): reverse('home'),
            reverse('bank'): reverse('home'),
            reverse('student_create'): reverse('student_list'),
            reverse('student_edit', args=[self.student.pk]): reverse('student_list'),
            reverse('student_progress', args=[self.student.pk]): reverse('student_list'),
            reverse('material_detail', args=[self.material.pk]): reverse('bank'),
            reverse('lesson_detail', args=[self.lesson.pk]): reverse('bank'),
            reverse('adaptation_edit', args=[self.adaptation.pk]): material,
            reverse('evaluation', args=[self.adaptation.pk]): material,
        }
        for url, parent in pages.items():
            with self.subTest(url=url):
                self.assertEqual(self.back(url), [parent])

    def test_editing_from_a_class_returns_to_that_class(self):
        lesson = reverse('lesson_detail', args=[self.lesson.pk])
        url = f"{reverse('adaptation_edit', args=[self.adaptation.pk])}?next={lesson}"
        self.assertEqual(self.back(url), [lesson])
        outside = f"{reverse('adaptation_edit', args=[self.adaptation.pk])}?next=https://otro-sitio.example/"
        self.assertNotIn('otro-sitio', self.back(outside)[0])  # never leaves the app

    def test_the_script_goes_back_only_to_a_previous_page_of_the_app(self):
        script = (Path(settings.BASE_DIR) / 'static' / 'js' / 'forms.js').read_text(encoding='utf-8')
        for rule in ("closest('[data-back]')", 'previous.origin === here.origin', "startsWith('/cuenta/')",
                     'samePage', 'window.history.back()'):
            self.assertIn(rule, script)
