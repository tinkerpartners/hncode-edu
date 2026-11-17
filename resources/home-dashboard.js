/* The page may be replaced by the site's AJAX navigator. */
(function () {
    const shortcuts = document.querySelector('.home-mobile-shortcuts:not([data-shortcuts-ready])');
    if (shortcuts) {
        shortcuts.dataset.shortcutsReady = 'true';
        shortcuts.addEventListener('click', function (event) {
            const link = event.target.closest('a');
            if (!link || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
            const heading = document.getElementById(link.hash.slice(1));
            if (!heading) return;
            event.preventDefault();
            heading.tabIndex = -1;
            heading.focus({preventScroll: true});
            heading.scrollIntoView({block: 'start'});
        });
    }

    document.querySelectorAll('[data-home-tabs]:not([data-tabs-ready])').forEach(function (widget) {
        widget.dataset.tabsReady = 'true';
        const tabs = Array.from(widget.querySelectorAll('[role=tab]'));

        function select(selected) {
            for (const tab of tabs) {
                const active = tab === selected;
                tab.setAttribute('aria-selected', String(active));
                tab.tabIndex = active ? 0 : -1;
                document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
            }
        }

        widget.addEventListener('click', function (event) {
            const tab = event.target.closest('[role=tab]');
            if (tabs.includes(tab)) select(tab);
        });

        widget.addEventListener('keydown', function (event) {
            const index = tabs.indexOf(event.target);
            if (index === -1) return;
            let next;
            switch (event.key) {
                case 'ArrowLeft': next = (index + tabs.length - 1) % tabs.length; break;
                case 'ArrowRight': next = (index + 1) % tabs.length; break;
                case 'Home': next = 0; break;
                case 'End': next = tabs.length - 1; break;
                default: return;
            }
            event.preventDefault();
            select(tabs[next]);
            tabs[next].focus();
        });
    });
})();
