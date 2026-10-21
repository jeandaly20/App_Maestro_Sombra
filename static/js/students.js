// Student registry: live age and directory filters.
// Names are encrypted in the database, so filtering happens here, in the teacher's browser.
'use strict';

document.addEventListener('DOMContentLoaded', () => {
    // Shows the age as soon as the birth date is chosen
    document.querySelectorAll('[data-age-for]').forEach((hint) => {
        const input = document.getElementById(hint.dataset.ageFor);
        if (!input) return;
        const update = () => {
            if (!input.value) {
                hint.textContent = '';
                return;
            }
            const born = new Date(`${input.value}T00:00:00`);
            const today = new Date();
            let age = today.getFullYear() - born.getFullYear();
            const birthdayPassed = today.getMonth() > born.getMonth()
                || (today.getMonth() === born.getMonth() && today.getDate() >= born.getDate());
            if (!birthdayPassed) age -= 1;
            hint.textContent = age >= 0 ? `Edad: ${age} años (calculada automáticamente)` : '';
        };
        input.addEventListener('input', update);
        update();
    });

    // Directory: search box + group chips
    const cards = [...document.querySelectorAll('[data-student]')];
    const search = document.querySelector('[data-directory-search]');
    const chips = [...document.querySelectorAll('[data-filter]')];
    const noResults = document.querySelector('[data-no-results]');
    if (!cards.length) return;

    let group = 'all';
    const apply = () => {
        const text = (search ? search.value : '').trim().toLowerCase();
        let visible = 0;
        cards.forEach((card) => {
            const inGroup = group === 'all' || card.dataset.groups.split(' ').includes(group);
            const matches = !text || card.dataset.search.includes(text);
            card.hidden = !(inGroup && matches);
            if (!card.hidden) visible += 1;
        });
        if (noResults) noResults.hidden = visible > 0;
    };

    chips.forEach((chip) => {
        chip.addEventListener('click', () => {
            group = chip.dataset.filter;
            chips.forEach((other) => other.setAttribute('aria-pressed', String(other === chip)));
            apply();
        });
    });
    if (search) search.addEventListener('input', apply);
});
