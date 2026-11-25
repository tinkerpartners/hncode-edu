(() => {
    // Apply catalog filters as soon as a value changes; the button stays for no-JS use.
    document.querySelectorAll('.exam-toolbar').forEach(form => {
        const apply = form.querySelector('[data-exam-filter-apply]');
        if (!apply) return;
        apply.hidden = true;
        const selects = form.querySelectorAll('.exam-filter-fields select');
        selects.forEach(select => {
            select.addEventListener('change', () => {
                // Leave empty filters out of the address so links stay short.
                selects.forEach(field => { field.disabled = !field.value; });
                form.submit();
            });
        });
        window.addEventListener('pageshow', () => {
            selects.forEach(field => { field.disabled = false; });
        });
    });
})();
