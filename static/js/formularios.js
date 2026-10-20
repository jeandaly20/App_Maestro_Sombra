// Ayudas de los formularios: mostrar contraseña, medidor de seguridad y confirmación.
'use strict';

document.addEventListener('DOMContentLoaded', () => {
    // Botón "ojo" para mostrar u ocultar la contraseña
    document.querySelectorAll('[data-mostrar-clave]').forEach((boton) => {
        const campo = document.getElementById(boton.dataset.mostrarClave);
        if (!campo) return;
        boton.addEventListener('click', () => {
            const oculto = campo.type === 'password';
            campo.type = oculto ? 'text' : 'password';
            boton.setAttribute('aria-pressed', String(oculto));
            boton.setAttribute('aria-label', oculto ? 'Ocultar contraseña' : 'Mostrar contraseña');
            boton.querySelector('.icono').textContent = oculto ? 'visibility_off' : 'visibility';
        });
    });

    // Medidor de seguridad de la contraseña (mismas reglas que valida el servidor)
    const medidor = document.querySelector('[data-medidor]');
    if (medidor) {
        const campo = document.getElementById(medidor.dataset.medidor);
        const estado = medidor.querySelector('[data-medidor-estado]');
        const reglas = {
            longitud: (v) => v.length >= 8,
            numero: (v) => /\d/.test(v),
            mayuscula: (v) => /[A-ZÁÉÍÓÚÑ]/.test(v),
            simbolo: (v) => /[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]/.test(v),
        };
        const textos = ['Por ingresar', 'Débil', 'Aceptable', 'Buena', 'Fuerte'];

        campo.addEventListener('input', () => {
            const valor = campo.value;
            let nivel = 0;
            Object.entries(reglas).forEach(([nombre, cumple]) => {
                const ok = cumple(valor);
                if (ok) nivel += 1;
                const item = medidor.querySelector(`[data-regla="${nombre}"]`);
                if (item) {
                    item.classList.toggle('cumple', ok);
                    item.querySelector('.icono').textContent = ok ? 'check_circle' : 'radio_button_unchecked';
                }
            });
            if (!valor) nivel = 0;
            medidor.dataset.nivel = String(nivel);
            estado.textContent = textos[nivel];
        });
    }

    // Aviso inmediato si la confirmación no coincide
    document.querySelectorAll('[data-confirma]').forEach((confirmacion) => {
        const original = document.getElementById(confirmacion.dataset.confirma);
        const validar = () => {
            const distinto = confirmacion.value && confirmacion.value !== original.value;
            confirmacion.setCustomValidity(distinto ? 'Las contraseñas no coinciden.' : '');
        };
        confirmacion.addEventListener('input', validar);
        original.addEventListener('input', validar);
    });

    // Menú lateral en móviles
    const botonMenu = document.querySelector('[data-abrir-menu]');
    const menu = document.getElementById('menu-lateral');
    if (botonMenu && menu) {
        botonMenu.addEventListener('click', () => {
            const abierto = menu.dataset.abierto === 'true';
            menu.dataset.abierto = String(!abierto);
            botonMenu.setAttribute('aria-expanded', String(!abierto));
        });
    }
});
