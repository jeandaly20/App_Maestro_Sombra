// Student registry: live age, directory filters and leave confirmation.
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
            const minAge = Number(input.dataset.minAge), maxAge = Number(input.dataset.maxAge);
            const outside = age < minAge || age > maxAge;
            hint.classList.toggle('age-hint--invalid', outside);
            hint.textContent = outside
                ? `Edad: ${age} años · fuera del rango permitido (${minAge} a ${maxAge} años)`
                : `Edad: ${age} años (calculada automáticamente)`;
        };
        input.addEventListener('input', update);
        update();
    });

    // "In evaluation" has no report yet: its date and file are hidden and cleared
    const source = document.getElementById('id_diagnosis_source');
    const details = document.querySelectorAll('[data-diagnosis-details]');
    if (source && details.length) {
        const toggleDetails = () => {
            const inEvaluation = source.value === 'IN_EVALUATION';
            details.forEach((block) => {
                block.hidden = inEvaluation;
                if (inEvaluation) {
                    block.querySelectorAll('input[type="date"], input[type="file"]').forEach((field) => { field.value = ''; });
                }
            });
        };
        source.addEventListener('change', toggleDetails);
        toggleDetails();
    }

    // Conditions that cannot go together are disabled as soon as one of them is ticked
    // (same rules as the model and the database: DSM-5 criterion D, one intellectual disability level)
    const learningDisorders = ['DYSLEXIA', 'DYSCALCULIA'];
    const intellectual = ['ID_MILD', 'ID_MODERATE'];
    const conditionBoxes = [...document.querySelectorAll('input[name="conditions"]')];
    const conflictNote = document.querySelector('[data-condition-conflict]');
    if (conditionBoxes.length) {
        const blockConflicts = () => {
            const chosen = conditionBoxes.filter((box) => box.checked).map((box) => box.value);
            const hasLearning = chosen.some((value) => learningDisorders.includes(value));
            const chosenLevel = chosen.find((value) => intellectual.includes(value));
            conditionBoxes.forEach((box) => {
                const blocked = !box.checked && (
                    (learningDisorders.includes(box.value) && chosenLevel)
                    || (intellectual.includes(box.value) && (hasLearning || chosenLevel)));
                box.disabled = Boolean(blocked);
            });
            let note = '';
            if (hasLearning) note = 'La discapacidad intelectual no se combina con dislexia o discalculia (DSM-5).';
            else if (chosenLevel) note = 'Con discapacidad intelectual no se registran dislexia ni discalculia, y solo un nivel.';
            conflictNote.textContent = note;
            conflictNote.hidden = !note;
        };
        conditionBoxes.forEach((box) => box.addEventListener('change', blockConflicts));
        blockConflicts();
    }

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
