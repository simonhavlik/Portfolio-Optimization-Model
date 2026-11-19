const table = document.getElementById('manager-ranking');
const search = document.getElementById('manager-filter');
if (table && search) {
    const body = table.tBodies[0];
    const rows = Array.from(body.rows);
    const empty = document.getElementById('manager-empty');
    search.closest('label').hidden = false;
    search.addEventListener('input', () => {
        const query = search.value.trim().toLocaleLowerCase();
        for (const row of rows) {
            row.hidden = !row.dataset.manager.toLocaleLowerCase().includes(query);
        }
        empty.hidden = rows.some(row => !row.hidden);
    });

    const sortable = [...table.querySelectorAll('th[data-sort]')];
    for (const column of sortable) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = column.textContent.trim();
        column.replaceChildren(button);
        button.addEventListener('click', () => {
            const direction = column.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending';
            for (const header of sortable) header.removeAttribute('aria-sort');
            column.setAttribute('aria-sort', direction);
            const index = column.cellIndex;
            rows.sort((a, b) => {
                const first = a.cells[index].dataset.value;
                const second = b.cells[index].dataset.value;
                if (first === '' || second === '') return Number(first === '') - Number(second === '');
                return direction === 'ascending' ? Number(first) - Number(second) : Number(second) - Number(first);
            });
            body.append(...rows);
        });
    }
}

if (navigator.clipboard) {
    for (const button of document.querySelectorAll('[data-copy]')) {
        button.hidden = false;
        button.addEventListener('click', async () => {
            const draft = document.getElementById(button.dataset.copy);
            try {
                await navigator.clipboard.writeText(draft.textContent.trim());
                button.textContent = 'Copied';
            } catch {
                button.textContent = 'Copy unavailable';
            }
        });
    }
}
