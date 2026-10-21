// Form helpers: show password, strength meter and confirmation check.
'use strict';

document.addEventListener('DOMContentLoaded', () => {
    // "Eye" button to show or hide the password
    document.querySelectorAll('[data-toggle-password]').forEach((button) => {
        const input = document.getElementById(button.dataset.togglePassword);
        if (!input) return;
        button.addEventListener('click', () => {
            const hidden = input.type === 'password';
            input.type = hidden ? 'text' : 'password';
            button.setAttribute('aria-pressed', String(hidden));
            button.setAttribute('aria-label', hidden ? 'Ocultar contraseña' : 'Mostrar contraseña');
            button.querySelector('.icon').textContent = hidden ? 'visibility_off' : 'visibility';
        });
    });

    // Password strength meter (same rules the server validates)
    const meter = document.querySelector('[data-strength-meter]');
    if (meter) {
        const input = document.getElementById(meter.dataset.strengthMeter);
        const status = meter.querySelector('[data-meter-status]');
        const rules = {
            length: (v) => v.length >= 8,
            number: (v) => /\d/.test(v),
            uppercase: (v) => /[A-ZÁÉÍÓÚÑ]/.test(v),
            symbol: (v) => /[^A-Za-z0-9ÁÉÍÓÚÑáéíóúñ]/.test(v),
        };
        const labels = ['Por ingresar', 'Débil', 'Aceptable', 'Buena', 'Fuerte'];

        input.addEventListener('input', () => {
            const value = input.value;
            let level = 0;
            Object.entries(rules).forEach(([name, passes]) => {
                const ok = passes(value);
                if (ok) level += 1;
                const item = meter.querySelector(`[data-rule="${name}"]`);
                if (item) {
                    item.classList.toggle('met', ok);
                    item.querySelector('.icon').textContent = ok ? 'check_circle' : 'radio_button_unchecked';
                }
            });
            if (!value) level = 0;
            meter.dataset.level = String(level);
            status.textContent = labels[level];
        });
    }

    // Immediate warning when the confirmation does not match
    document.querySelectorAll('[data-confirm]').forEach((confirmation) => {
        const original = document.getElementById(confirmation.dataset.confirm);
        const validate = () => {
            const differs = confirmation.value && confirmation.value !== original.value;
            confirmation.setCustomValidity(differs ? 'Las contraseñas no coinciden.' : '');
        };
        confirmation.addEventListener('input', validate);
        original.addEventListener('input', validate);
    });

    // Sidebar on mobile
    const menuButton = document.querySelector('[data-open-menu]');
    const sidebar = document.getElementById('sidebar');
    if (menuButton && sidebar) {
        menuButton.addEventListener('click', () => {
            const open = sidebar.dataset.open === 'true';
            sidebar.dataset.open = String(!open);
            menuButton.setAttribute('aria-expanded', String(!open));
        });
    }
});
