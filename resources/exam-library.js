(() => {
    const dialog = document.getElementById('exam-confirm-dialog');
    if (!dialog || typeof dialog.showModal !== 'function') return;

    const body = dialog.querySelector('[data-exam-confirm-body]');
    let pending;

    dialog.addEventListener('close', () => {
        if (dialog.open) return;
        if (pending) pending.abort();
        body.replaceChildren();
    });
    window.addEventListener('pageshow', event => {
        if (event.persisted && dialog.open) dialog.close();
    });

    document.addEventListener('click', async event => {
        if (event.target.closest('[data-exam-confirm-cancel]')) {
            dialog.close();
            return;
        }
        const link = event.target.closest('a[data-exam-confirm]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey
            || event.metaKey || event.altKey || event.shiftKey || dialog.open) return;

        event.preventDefault();
        if (pending) pending.abort();
        const controller = new AbortController();
        pending = controller;
        const status = document.createElement('p');
        status.className = 'exam-confirm-note';
        status.setAttribute('role', 'status');
        status.textContent = dialog.dataset.loadingText;
        body.replaceChildren(status);
        dialog.showModal();

        try {
            const response = await fetch(link.href, {
                headers: {'X-Exam-Confirmation': '1'}, signal: controller.signal,
            });
            if (controller.signal.aborted) return;
            if (response.headers.get('X-Exam-Confirmation') !== '1') {
                window.location.assign(response.redirected ? response.url : link.href);
                return;
            }
            const markup = await response.text();
            if (!controller.signal.aborted) body.innerHTML = markup;
        } catch (error) {
            if (controller.signal.aborted) return;
            status.textContent = `${dialog.dataset.errorText} `;
            const retry = document.createElement('a');
            retry.href = link.href;
            retry.textContent = dialog.dataset.retryText;
            status.append(retry);
        } finally {
            if (pending === controller) pending = undefined;
        }
    });
})();
