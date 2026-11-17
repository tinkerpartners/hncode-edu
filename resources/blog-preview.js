(function () {
    const feed = document.querySelector('.article-feed[data-feed-items]');
    if (!feed || feed.dataset.articlePreviewsReady || !window.ResizeObserver) return;
    feed.dataset.articlePreviewsReady = 'true';

    function setExpanded(preview, button, expanded) {
        button.setAttribute('aria-expanded', String(expanded));
        preview.toggleAttribute('data-collapsed', !expanded);
    }

    function updatePreview(preview) {
        const button = preview.parentElement.querySelector('[data-article-toggle]');
        const rootFontSize = parseFloat(getComputedStyle(document.documentElement).fontSize);
        const maxHeight = parseFloat(getComputedStyle(preview).getPropertyValue('--home-preview-rem')) * rootFontSize;
        const overflows = preview.firstElementChild.offsetHeight > maxHeight + 1;
        const expanded = button.getAttribute('aria-expanded') === 'true';

        // Keep a focused toggle available until the reader leaves it.
        button.hidden = !overflows && button !== document.activeElement;
        preview.toggleAttribute('data-collapsed', overflows && !expanded);
    }

    const sizes = new ResizeObserver(function (entries) {
        if (!feed.isConnected) {
            sizes.disconnect();
            cards.disconnect();
            return;
        }
        for (const {target} of entries) {
            if (feed.contains(target)) updatePreview(target.parentElement);
            else sizes.unobserve(target);
        }
    });

    const cards = new MutationObserver(function (changes) {
        for (const {addedNodes, removedNodes} of changes) {
            for (const node of removedNodes) {
                const summary = node.querySelector?.('[data-article-preview] > .summary');
                if (summary) sizes.unobserve(summary);
            }
            for (const node of addedNodes) {
                const summary = node.querySelector?.('[data-article-preview] > .summary');
                if (summary) sizes.observe(summary);
            }
        }
    });
    cards.observe(feed, {childList: true});
    feed.querySelectorAll('[data-article-preview] > .summary').forEach(summary => sizes.observe(summary));

    feed.addEventListener('click', function (event) {
        const button = event.target.closest('[data-article-toggle]');
        if (!button) return;
        const preview = button.parentElement.querySelector('[data-article-preview]');
        const expanded = button.getAttribute('aria-expanded') === 'true';
        setExpanded(preview, button, !expanded);
        if (expanded) {
            updatePreview(preview);
            button.scrollIntoView({block: 'nearest'});
        }
    });

    feed.addEventListener('focusin', function (event) {
        const preview = event.target.closest('[data-article-preview][data-collapsed]');
        if (!preview) return;
        const fadeInRem = parseFloat(getComputedStyle(preview).getPropertyValue('--home-preview-fade-rem'));
        const fade = parseFloat(getComputedStyle(document.documentElement).fontSize) * fadeInRem;
        if (event.target.getBoundingClientRect().bottom > preview.getBoundingClientRect().bottom - fade) {
            const button = preview.parentElement.querySelector('[data-article-toggle]');
            setExpanded(preview, button, true);
            event.target.scrollIntoView({block: 'nearest'});
        }
    });

    feed.addEventListener('focusout', function (event) {
        const button = event.target.closest('[data-article-toggle]');
        if (!button) return;
        const preview = button.parentElement.querySelector('[data-article-preview]');
        updatePreview(preview);
    });
})();
