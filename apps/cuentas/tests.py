from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import Docente

CLAVE = 'Aula-Inclusiva-2026'


def datos_registro(**cambios):
    datos = {
        'nombre_completo': 'Mariana Valenzuela Ríos',
        'email': 'Mariana@Correo.com',
        'password1': CLAVE,
        'password2': CLAVE,
        'especialidad': 'MAESTRO_SOMBRA',
        'acepta_terminos': 'on',
    }
    datos.update(cambios)
    return datos


class RegistroTests(TestCase):

    def test_registro_crea_cuenta_e_inicia_sesion(self):
        respuesta = self.client.post(reverse('registro'), datos_registro())
        self.assertRedirects(respuesta, reverse('inicio'))
        docente = Docente.objects.get()
        self.assertEqual(docente.email, 'mariana@correo.com')
        self.assertTrue(docente.acepta_terminos)
        self.assertIsNotNone(docente.fecha_aceptacion_terminos)
        self.assertNotEqual(docente.password, CLAVE)  # se guarda el hash
        self.assertEqual(self.client.session['_auth_user_id'], str(docente.pk))

    def test_sin_aceptar_terminos_no_crea_cuenta(self):
        datos = datos_registro()
        del datos['acepta_terminos']
        respuesta = self.client.post(reverse('registro'), datos)
        self.assertContains(respuesta, 'Debes aceptar los términos')
        self.assertFalse(Docente.objects.exists())

    def test_correo_repetido_con_mayusculas(self):
        Docente.objects.create_user('mariana@correo.com', CLAVE, nombre_completo='Otra Persona')
        respuesta = self.client.post(reverse('registro'), datos_registro(email='MARIANA@correo.com'))
        self.assertContains(respuesta, 'Ya existe una cuenta con este correo')

    def test_contrasenas_distintas(self):
        respuesta = self.client.post(reverse('registro'), datos_registro(password2='Otra-Clave-2026'))
        self.assertContains(respuesta, 'Las contraseñas no coinciden')

    def test_contrasena_debil(self):
        respuesta = self.client.post(reverse('registro'), datos_registro(password1='12345678', password2='12345678'))
        self.assertFalse(Docente.objects.exists())
        self.assertContains(respuesta, 'aria-invalid="true"')

    def test_nombre_sin_apellido(self):
        respuesta = self.client.post(reverse('registro'), datos_registro(nombre_completo='Mariana'))
        self.assertContains(respuesta, 'al menos un nombre y un apellido')


class IngresoTests(TestCase):

    def setUp(self):
        cache.clear()
        self.docente = Docente.objects.create_user('ana@correo.com', CLAVE, nombre_completo='Ana Pérez')

    def ingresar(self, clave=CLAVE, **extra):
        return self.client.post(reverse('login'), {'username': 'ANA@correo.com', 'password': clave, **extra})

    def test_ingreso_correcto_sin_recordar(self):
        respuesta = self.ingresar()
        self.assertRedirects(respuesta, reverse('inicio'))
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_mantener_sesion_iniciada(self):
        self.ingresar(recordar='on')
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.session.get_expiry_age(), 60 * 60 * 24 * 14)

    def test_clave_incorrecta(self):
        respuesta = self.ingresar(clave='mala')
        self.assertContains(respuesta, 'El correo o la contraseña no son correctos')

    def test_bloqueo_tras_cinco_intentos(self):
        for _ in range(5):
            self.ingresar(clave='mala')
        respuesta = self.ingresar()  # aun con la clave correcta
        self.assertContains(respuesta, 'Demasiados intentos fallidos')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_inicio_exige_sesion(self):
        respuesta = self.client.get(reverse('inicio'))
        self.assertRedirects(respuesta, f"{reverse('login')}?next=/")

    def test_cerrar_sesion(self):
        self.ingresar()
        respuesta = self.client.post(reverse('salir'))
        self.assertRedirects(respuesta, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)


class RecuperarTests(TestCase):

    def test_envia_correo_y_cambia_clave(self):
        Docente.objects.create_user('ana@correo.com', CLAVE, nombre_completo='Ana Pérez')
        self.client.post(reverse('recuperar'), {'email': 'ana@correo.com'})
        self.assertEqual(len(mail.outbox), 1)
        enlace = next(linea for linea in mail.outbox[0].body.splitlines() if '/recuperar/' in linea)
        ruta = enlace.split('testserver')[1]
        formulario = self.client.get(ruta, follow=True)
        nueva = 'Nueva-Clave-Segura-9'
        respuesta = self.client.post(formulario.redirect_chain[-1][0],
                                     {'new_password1': nueva, 'new_password2': nueva})
        self.assertRedirects(respuesta, reverse('login'))
        self.assertTrue(Docente.objects.get().check_password(nueva))

    def test_correo_inexistente_no_revela_nada(self):
        respuesta = self.client.post(reverse('recuperar'), {'email': 'nadie@correo.com'})
        self.assertRedirects(respuesta, reverse('recuperar_enviado'))
        self.assertEqual(len(mail.outbox), 0)
