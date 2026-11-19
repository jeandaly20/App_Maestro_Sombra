// Form helpers: show password, strength meter and confirmation check.
'use strict';

// "Volver": goes back to the previous page of the app, so the teacher lands where they were (with the same
// filters and scroll). It follows its own link instead (the page's parent) when there is no such page: opened
// directly or from another site, coming from the sign-in, or the same page reloaded after a form error.
document.addEventListener('click', (event) => {
    const link = event.target.closest('[data-back]');
    if (!link || event.defaultPrevented || event.button !== 0
        || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    let previous;
    try {
        previous = new URL(document.referrer);
    } catch (error) {
        return;  // no previous page: the link's own address
    }
    const here = new URL(window.location.href);
    const fromApp = previous.origin === here.origin && !previous.pathname.startsWith('/cuenta/');
    const samePage = previous.pathname + previous.search === here.pathname + here.search;
    if (fromApp && !samePage && window.history.length > 1) {
        event.preventDefault();
        window.history.back();
    }
});

// A file the previous action prepared (the report of an evaluation just finished) downloads by itself, once
document.addEventListener('DOMContentLoaded', () => {
    const download = document.querySelector('[data-auto-download]');
    if (download) window.location.assign(download.href);
});

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
    document.querySelectorAll('input[data-confirm]').forEach((confirmation) => {
        const original = document.getElementById(confirmation.dataset.confirm);
        const validate = () => {
            const differs = confirmation.value && confirmation.value !== original.value;
            confirmation.setCustomValidity(differs ? 'Las contraseñas no coinciden.' : '');
        };
        confirmation.addEventListener('input', validate);
        original.addEventListener('input', validate);
    });

    // Actions that remove something (a student's leave, deleting an adaptation) ask first
    document.querySelectorAll('form[data-confirm]').forEach((form) => {
        form.addEventListener('submit', (event) => {
            if (!window.confirm(form.dataset.confirm)) event.preventDefault();
        });
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

// ---------- Work in progress survives a reload, a lost connection or a power cut ----------
// A form marked data-draft keeps what the teacher typed or chose in this browser (localStorage) while it is not
// confirmed by the server: after a reload, a power cut or a send that failed without internet, it is put back
// with a notice and can be discarded. To protect the minors' data it is tied to the teacher's account (another
// teacher on the same computer never sees it), it expires after DRAFT_HOURS and it is removed when the server
// confirms the form and when the teacher signs out. Files cannot be put back by a browser: the notice asks for
// them again. Without internet the form is not sent (the work stays here) and leaving with changes asks first.
const DRAFT_PREFIX = 'draft:';
const DRAFT_HOURS = 12;
const DRAFT_SKIPPED = ['file', 'password', 'hidden', 'submit', 'button', 'reset'];

function draftStore() {
    try {
        const store = window.localStorage;
        store.setItem(`${DRAFT_PREFIX}test`, '1');
        store.removeItem(`${DRAFT_PREFIX}test`);
        return store;
    } catch (error) {
        return null;  // private mode or blocked storage: the form works as before
    }
}

function draftKeys(store) {
    return Object.keys(store).filter((key) => key.startsWith(DRAFT_PREFIX));
}

function readDraft(store, key) {
    try {
        return JSON.parse(store.getItem(key) || 'null');
    } catch (error) {
        store.removeItem(key);
        return null;
    }
}

function draftFields(form) {
    return [...form.elements].filter((field) => field.name && !field.disabled && !field.closest('[data-no-draft]')
        && !DRAFT_SKIPPED.includes(field.type));
}

function draftValues(form) {
    const values = {};
    draftFields(form).forEach((field) => {
        if (field.type === 'checkbox' || field.type === 'radio') {
            if (!values[field.name]) values[field.name] = [];
            if (field.checked) values[field.name].push(field.value);
        } else if (field.tagName === 'SELECT' && field.multiple) {
            values[field.name] = [...field.selectedOptions].map((option) => option.value);
        } else {
            values[field.name] = field.value;
        }
    });
    return values;
}

function hasFiles(form) {
    return [...form.querySelectorAll('input[type="file"]')].some((input) => input.files.length);
}

function applyDraft(form, values) {
    draftFields(form).forEach((field) => {
        if (!(field.name in values)) return;
        const saved = values[field.name];
        let changed = false;
        if (field.type === 'checkbox' || field.type === 'radio') {
            const checked = saved.includes(field.value);
            changed = field.checked !== checked;
            field.checked = checked;
        } else if (field.tagName === 'SELECT' && field.multiple) {
            [...field.options].forEach((option) => { option.selected = saved.includes(option.value); });
            changed = true;
        } else if (field.value !== saved) {
            field.value = saved;
            changed = true;
        }
        // The page's own scripts (tabs, rules per student, age...) react as if the teacher did it
        if (changed) {
            field.dispatchEvent(new Event('input', { bubbles: true }));
            field.dispatchEvent(new Event('change', { bubbles: true }));
        }
    });
}

function minutesAgo(time) {
    const minutes = Math.max(0, Math.round((Date.now() - time) / 60000));
    if (minutes < 1) return 'hace un momento';
    if (minutes < 60) return `hace ${minutes} min`;
    return `hace ${Math.round(minutes / 60)} h`;
}

function formNotice(form, kind, icon, text, button) {
    form.querySelector(`.draft-notice--${kind}`)?.remove();
    const notice = document.createElement('div');
    notice.className = `alert alert--${kind === 'offline' ? 'error' : 'info'} draft-notice draft-notice--${kind}`;
    notice.setAttribute('role', kind === 'offline' ? 'alert' : 'status');
    notice.innerHTML = `<span class="icon" aria-hidden="true">${icon}</span><p></p>`;
    notice.querySelector('p').textContent = text;
    if (button) {
        const action = document.createElement('button');
        action.type = 'button';
        action.className = 'button button--ghost';
        action.textContent = button.label;
        action.addEventListener('click', button.onClick);
        notice.append(action);
    }
    form.prepend(notice);
    return notice;
}

document.addEventListener('DOMContentLoaded', () => {
    const store = draftStore();
    if (!store) return;
    const user = document.body.dataset.user || 'anonimo';
    const here = window.location.pathname;
    // The server answered the last send of this page: a message (saved, error) or the errors of a field
    const answered = Boolean(document.querySelector('[data-server-message], .field__error'));
    // Old drafts expire; a sent draft of another page means the server took it and moved on
    draftKeys(store).forEach((key) => {
        const draft = readDraft(store, key);
        const expired = !draft || Date.now() - (draft.savedAt || 0) > DRAFT_HOURS * 3600000;
        const movedOn = draft && draft.sent && draft.user === user && draft.path !== here;
        if (expired || movedOn) store.removeItem(key);
    });
    const forms = [...document.querySelectorAll('form[data-draft]')];
    if (!forms.length) return;
    let leaving = false;
    forms.forEach((form) => {
        const key = `${DRAFT_PREFIX}${user}:${here}:${form.dataset.draft}`;
        const initial = JSON.stringify(draftValues(form));
        let dirty = false;
        const saved = readDraft(store, key);
        const discard = () => {
            store.removeItem(key);
            leaving = true;
            window.location.reload();
        };
        if (saved && saved.sent && answered) {
            store.removeItem(key);  // the server received it: the page shows its answer
        } else if (saved && saved.values && JSON.stringify(saved.values) !== initial) {
            applyDraft(form, saved.values);
            dirty = true;
            const files = saved.files ? ' Vuelve a elegir los archivos: por seguridad, el navegador no los conserva.' : '';
            const text = saved.sent
                ? `No se pudo enviar (sin conexión o se cortó la luz). Recuperamos lo que tenías (${minutesAgo(saved.savedAt)}): revísalo y vuelve a enviarlo.${files}`
                : `Recuperamos lo que tenías sin guardar (${minutesAgo(saved.savedAt)}).${files}`;
            formNotice(form, 'restored', 'restore', text, { label: 'Descartar y volver a empezar', onClick: discard });
        } else if (saved) {
            store.removeItem(key);
        }
        const save = (sent) => {
            const values = draftValues(form);
            const files = hasFiles(form);
            dirty = sent || JSON.stringify(values) !== initial || files;
            if (dirty) store.setItem(key, JSON.stringify({ values, files, user, path: here, savedAt: Date.now(), sent }));
            else store.removeItem(key);
        };
        let timer;
        const remember = () => {
            window.clearTimeout(timer);
            timer = window.setTimeout(() => save(false), 300);
        };
        form.addEventListener('input', remember);
        form.addEventListener('change', remember);
        form.addEventListener('submit', (event) => {
            if (event.defaultPrevented) return;
            leaving = true;
            window.clearTimeout(timer);
            save(true);  // kept until the server answers: if the send is cut, the next visit puts it back
        });
        form.draftIsDirty = () => dirty || hasFiles(form);
    });
    // Leaving with changes not sent (or files chosen) asks first; sending the form or discarding does not
    window.addEventListener('beforeunload', (event) => {
        if (leaving || !forms.some((form) => form.draftIsDirty())) return;
        event.preventDefault();
        event.returnValue = '';
    });
});

// Without internet a form is not sent: the work stays in the page (and in its draft) until the connection returns.
// In the capture phase, so no other script (a "working…" overlay, a disabled button) starts first.
document.addEventListener('submit', (event) => {
    const form = event.target;
    if (navigator.onLine !== false || form.matches('form[action$="/cuenta/salir/"]')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const notice = formNotice(form, 'offline', 'wifi_off', 'Sin conexión a internet: no se envió. Tu trabajo sigue '
        + 'aquí guardado; vuelve a intentarlo cuando regrese la conexión.');
    notice.scrollIntoView({ block: 'nearest' });
    window.addEventListener('online', () => {
        notice.querySelector('p').textContent = 'Volvió la conexión: ya puedes enviarlo.';
        notice.className = 'alert alert--success draft-notice draft-notice--offline';
    }, { once: true });
}, true);

// Signing out removes every draft kept in this browser
document.addEventListener('submit', (event) => {
    if (!event.target.matches('form[action$="/cuenta/salir/"]')) return;
    const store = draftStore();
    if (store) draftKeys(store).forEach((key) => store.removeItem(key));
});
