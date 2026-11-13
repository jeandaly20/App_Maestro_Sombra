"""
Task 3.1: the individual consent of the legal representative, per treatment, and its use as the gate for sending
the pedagogical profile (categories only) to the AI. Made-up students; Gemini simulated.
"""
from datetime import date
from importlib import import_module
from unittest import mock

from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Teacher
from apps.adaptations import ai, services
from apps.adaptations.tests import FakeGemini
from apps.materials.models import OriginalMaterial, Subject

from .models import (
    REGISTRATION_CONSENT_VERSION, REGISTRATION_TREATMENTS, ConsentStatus, Student, StudentConsent, Treatment,
    ai_profile_allowed, consent_allows,
)

BARRIERS = 'Le cuesta mantener la atención en actividades largas.'
SUPPORTS = 'Pausas programadas e instrucciones breves.'


@override_settings(DIAGNOSIS_DOCUMENT_REQUIRED=False)
class IndividualConsentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create_user('docente.consentimiento@maestrosombra.test', None,
                                                  full_name='Docente de Prueba', accepted_terms=True,
                                                  terms_accepted_at=timezone.now())
        cls.student = Student(teacher=cls.teacher, name='Estudiante de Prueba', birth_date=date(2016, 3, 1),
                              grade='EGB_5', conditions=['ADHD'], diagnosis_source='IN_EVALUATION',
                              learning_barriers=BARRIERS, pedagogical_supports=SUPPORTS)
        cls.student.save()

    def consent(self, treatments, **extra):
        fields = {'document_version': 'IA-PRUEBA-v1', 'authorized_on': date(2026, 11, 1), **extra}
        return StudentConsent.objects.create(student=self.student, treatments=treatments, **fields)

    @override_settings(AI_PROFILE_TRANSFER_ENABLED=True)
    def test_11_student_without_ai_authorization(self):
        self.assertFalse(consent_allows(self.student, Treatment.AI_PROFILE))
        self.assertFalse(ai_profile_allowed(self.student))

    def test_12_student_with_an_authorization_in_force(self):
        self.consent([Treatment.INTERNAL, Treatment.AI_ADAPTATION, Treatment.AI_PROFILE])
        self.assertTrue(consent_allows(self.student, Treatment.AI_PROFILE))
        self.assertFalse(ai_profile_allowed(self.student))  # the system setting is off by default
        with self.settings(AI_PROFILE_TRANSFER_ENABLED=True):
            self.assertTrue(ai_profile_allowed(self.student))
        other = Student(teacher=self.teacher, name='Otro Estudiante', birth_date=date(2016, 3, 1), grade='EGB_5',
                        conditions=['ADHD'], diagnosis_source='IN_EVALUATION')
        other.save()
        with self.settings(AI_PROFILE_TRANSFER_ENABLED=True):
            self.assertFalse(ai_profile_allowed(other))  # one student's consent never covers another

    @override_settings(AI_PROFILE_TRANSFER_ENABLED=True)
    def test_13_revoked_authorization(self):
        consent = self.consent([Treatment.INTERNAL, Treatment.AI_PROFILE])
        self.client.force_login(self.teacher)
        url = reverse('student_consent_revoke', args=[self.student.pk, consent.pk])
        intruder = Teacher.objects.create_user('intruso@maestrosombra.test', None, full_name='Intruso',
                                               accepted_terms=True, terms_accepted_at=timezone.now())
        self.client.force_login(intruder)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.client.force_login(self.teacher)
        self.assertRedirects(self.client.post(url), reverse('student_edit', args=[self.student.pk]))
        consent.refresh_from_db()
        self.assertEqual(consent.status, ConsentStatus.REVOKED)
        self.assertIsNotNone(consent.revoked_at)
        self.assertFalse(ai_profile_allowed(self.student))

    @override_settings(AI_PROFILE_TRANSFER_ENABLED=True)
    def test_14_an_old_authorization_does_not_cover_the_profile(self):
        self.consent(list(REGISTRATION_TREATMENTS), document_version=REGISTRATION_CONSENT_VERSION)
        self.assertTrue(consent_allows(self.student, Treatment.AI_ADAPTATION))
        self.assertFalse(ai_profile_allowed(self.student))

    def test_20_existing_authorizations_are_kept_with_their_own_scope(self):
        self.student.consent_file = 'students/x/y/autorizacion-prueba.bin'
        self.student.consent_file_type = 'image/jpeg'
        self.student.diagnosis_consent_at = timezone.now()
        Student.objects.filter(pk=self.student.pk).update(consent_file=self.student.consent_file,
                                                          consent_file_type='image/jpeg',
                                                          diagnosis_consent_at=self.student.diagnosis_consent_at)
        before = Student.objects.filter(pk=self.student.pk).values().get()
        migration = import_module('apps.students.migrations.0012_existing_consents')
        migration.keep_existing_consents(django_apps, None)
        migration.keep_existing_consents(django_apps, None)  # running it again creates nothing new
        consents = list(StudentConsent.objects.filter(student=self.student))
        self.assertEqual(len(consents), 1)
        self.assertEqual(consents[0].treatments, ['INTERNAL', 'AI_ADAPTATION'])  # never widened to the profile
        self.assertEqual(consents[0].signed_file, self.student.consent_file)
        self.assertEqual(Student.objects.filter(pk=self.student.pk).values().get(), before)  # the student is untouched

    def test_register_an_authorization_from_the_record(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse('student_consent_create', args=[self.student.pk]), {
            'document_version': 'IA-PERFIL-2027-v1', 'authorized_on': '2026-11-10',
            'treatments': ['INTERNAL', 'AI_ADAPTATION', 'AI_PROFILE'],
            'signed_document': SimpleUploadedFile('firma.jpg', b'\xff\xd8\xff' + b'0' * 50, content_type='image/jpeg')})
        self.assertRedirects(response, reverse('student_edit', args=[self.student.pk]))
        consent = StudentConsent.objects.get(student=self.student)
        self.assertTrue(consent.signed_file.endswith('.bin'))  # stored encrypted, the original name is not kept
        page = self.client.get(reverse('student_edit', args=[self.student.pk])).content.decode()
        self.assertIn('IA-PERFIL-2027-v1', page)
        self.assertIn('deshabilitado en el sistema', page)  # registered, but the setting is still off
        from django.core.files.storage import default_storage
        default_storage.delete(consent.signed_file)

    def test_the_profile_reaches_gemini_only_as_categories_and_only_when_authorized(self):
        material = OriginalMaterial.objects.create(teacher=self.teacher, subject=Subject.objects.get(code='LEN'),
                                                   title='Sustantivos', topic='El sustantivo', input_type='TEXT',
                                                   input_text='1. Subraya los sustantivos. 2. Escribe tres. 3. Lee. '
                                                              '4. Clasifica. 5. Explica.')

        def adapt():
            fake = FakeGemini()
            with mock.patch.object(ai, 'generate_json', fake):
                services.process_material(material.pk, [(self.student.pk, ['ADHD'])], {self.student.pk: []})
            material.adaptations.all().delete()
            return fake.everything()

        without = adapt()
        self.assertNotIn('Perfil pedagógico del estudiante', without)
        self.consent([Treatment.INTERNAL, Treatment.AI_ADAPTATION, Treatment.AI_PROFILE])
        with self.settings(AI_PROFILE_TRANSFER_ENABLED=True):
            allowed = adapt()
        self.assertIn('Perfil pedagógico del estudiante (categorías', allowed)
        self.assertIn('la atención sostenida', allowed)
        for private in (BARRIERS, SUPPORTS, 'Estudiante de Prueba'):
            self.assertNotIn(private, allowed)
