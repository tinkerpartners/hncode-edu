/*
 * Strict ("proctored") contest lockdown.
 *
 * Loaded by base.html only while the viewer holds a live participation in a
 * contest with strict mode on. Everything here is advisory -- a contestant can
 * open devtools and delete it. What it cannot do is buy them privacy: stopping
 * the heartbeat stops the server accepting their submissions (see
 * judge/utils/contest_strict.py).
 *
 * Two browser facts shape the whole design:
 *   1. requestFullscreen() only works inside a real user gesture, so fullscreen
 *      can never be entered from a timer, an AJAX callback, or on page load.
 *      Every entry is a button click.
 *   2. Navigating to another page exits fullscreen. Without the navigation
 *      handshake below, every in-contest click would look like an escape
 *      attempt and the whole field would be disqualified within minutes.
 */
(function () {
    'use strict';

    var configNode = document.getElementById('strict-contest-config');
    if (!configNode) return;

    var config;
    try {
        config = JSON.parse(configNode.textContent);
    } catch (e) {
        return;
    }
    if (!config || !config.eventUrl) return;

    var NAV_KEY = 'strict:nav:' + config.key;
    // A page load this soon after an in-contest click is our own navigation,
    // not an escape attempt.
    var NAV_WINDOW_MS = 8000;
    var CLIENT_DEDUPE_MS = 2000;

    var state = {
        violations: config.violations || 0,
        limit: config.limit,
        armed: !!config.armed,
        banned: false,
        deadline: null,
        countdownTimer: null,
        heartbeatTimer: null,
        lastReport: {},
        navPending: false,
        teardown: false
    };

    /* ---------------------------------------------------------------- utils */

    function fullscreenElement() {
        return document.fullscreenElement || document.webkitFullscreenElement ||
            document.mozFullScreenElement || document.msFullscreenElement || null;
    }

    function fullscreenSupported() {
        var el = document.documentElement;
        return !!(el.requestFullscreen || el.webkitRequestFullscreen ||
            el.mozRequestFullScreen || el.msRequestFullscreen);
    }

    function requestFullscreen() {
        var el = document.documentElement;
        var fn = el.requestFullscreen || el.webkitRequestFullscreen ||
            el.mozRequestFullScreen || el.msRequestFullscreen;
        if (!fn) return Promise.reject(new Error('unsupported'));
        try {
            var result = fn.call(el);
            return result && result.then ? result : Promise.resolve();
        } catch (e) {
            return Promise.reject(e);
        }
    }

    function csrfToken() {
        var input = document.querySelector('input[name=csrfmiddlewaretoken]');
        if (input) return input.value;
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function nonce() {
        return 'n' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    }

    // Local rather than the catalog's interpolate(): statici18n's bundle is
    // compiled per deployment and this file must not go dark if it is missing.
    function fmt(template, values) {
        return template.replace(/%\((\w+)\)s/g, function (match, name) {
            return Object.prototype.hasOwnProperty.call(values, name) ? values[name] : match;
        });
    }

    /* --------------------------------------------------------------- report */

    function applyState(data) {
        if (!data) return;
        if (data.ok === false && data.state === 'inactive') {
            teardown();
            return;
        }
        if (typeof data.violations === 'number') state.violations = data.violations;
        if (typeof data.limit === 'number') state.limit = data.limit;
        updateOverlayCounts();
        if (data.banned) {
            state.banned = true;
            teardown();
            window.location.href = data.redirect || config.contestUrl;
        }
    }

    function report(action, detail, options) {
        options = options || {};
        if (state.teardown || state.banned) return;

        // Collapse the burst one physical action produces: alt-tab raises blur
        // and visibilitychange and fullscreenchange. The server coalesces too --
        // this just saves the requests.
        if (!options.force) {
            var now = Date.now();
            var bucket = options.bucket || action;
            if (state.lastReport[bucket] && now - state.lastReport[bucket] < CLIENT_DEDUPE_MS) {
                return;
            }
            state.lastReport[bucket] = now;
        }

        var body = JSON.stringify({
            action: action,
            detail: detail || '',
            nonce: nonce()
        });

        if (options.keepalive && window.fetch) {
            // sendBeacon cannot set the CSRF header, so keepalive fetch it is.
            // Best-effort either way: browsers are free to drop unload work.
            try {
                window.fetch(config.eventUrl, {
                    method: 'POST',
                    keepalive: true,
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken()
                    },
                    body: body
                });
            } catch (e) { /* nothing useful to do during unload */ }
            return;
        }

        $.ajax({
            url: config.eventUrl,
            type: 'POST',
            contentType: 'application/json',
            headers: {'X-CSRFToken': csrfToken()},
            data: body,
            dataType: 'json'
        }).done(applyState);
    }

    /* ------------------------------------------------------------------- UI */

    var $arm, $overlay, $overlayCount, $overlayClock;

    function buildUI() {
        $arm = $(
            '<div id="strict-arm" class="strict-blocker" role="dialog" aria-modal="true">' +
            '<div class="strict-panel">' +
            '<h2 class="strict-title"></h2>' +
            '<p class="strict-body"></p>' +
            '<button type="button" class="strict-button"></button>' +
            '<p class="strict-note"></p>' +
            '</div></div>'
        ).appendTo(document.body).hide();

        $overlay = $(
            '<div id="strict-overlay" class="strict-blocker" role="alertdialog" aria-modal="true">' +
            '<div class="strict-panel strict-panel-warning">' +
            '<h2 class="strict-title"></h2>' +
            '<p class="strict-body"></p>' +
            '<p class="strict-clock"></p>' +
            '<p class="strict-count"></p>' +
            '<button type="button" class="strict-button"></button>' +
            '</div></div>'
        ).appendTo(document.body).hide();

        $overlayClock = $overlay.find('.strict-clock');
        $overlayCount = $overlay.find('.strict-count');

        $arm.find('.strict-title').text(gettext('Proctored contest'));
        $arm.find('.strict-button').text(gettext('Start proctored session'))
            .on('click', onArmClick);
        $overlay.find('.strict-title').text(gettext('Return to fullscreen'));
        $overlay.find('.strict-button').text(gettext('Return to fullscreen'))
            .on('click', onArmClick);
    }

    function showArm(message, note) {
        hideOverlay();
        $arm.find('.strict-body').text(message);
        $arm.find('.strict-note').text(note || '');
        $arm.show();
    }

    function hideArm() {
        $arm.hide();
    }

    function updateOverlayCounts() {
        if (!$overlayCount) return;
        if (config.autoban) {
            $overlayCount.text(fmt(gettext('Violation %(n)s of %(limit)s.'),
                {n: state.violations, limit: state.limit}));
        } else {
            $overlayCount.text(fmt(gettext('%(n)s violations recorded.'),
                {n: state.violations}));
        }
    }

    function hideOverlay() {
        if ($overlay) $overlay.hide();
    }

    /* -------------------------------------------------------------- arming */

    function onArmClick() {
        requestFullscreen().then(function () {
            // Fullscreen is live: stop the countdown before reporting, so a slow
            // request cannot let the timer fire on someone who did comply.
            stopCountdown();
            hideArm();
            hideOverlay();
            report(state.armed ? 'returned' : 'session_start', '', {force: true});
            state.armed = true;
            startHeartbeat();
        }).catch(function () {
            $arm.find('.strict-note').text(
                gettext('Your browser refused to enter fullscreen. Check that fullscreen is allowed for this site, then try again.')
            );
        });
    }

    /* ------------------------------------------------------------ countdown */

    function startCountdown() {
        if (state.deadline || !config.autoban) {
            // Monitor-only contests still get the warning, just no timer.
            if (!config.autoban) showReturnOverlay(null);
            return;
        }
        // Wall-clock deadline, never accumulated ticks: background tabs throttle
        // setInterval to about once a minute, which would pause the countdown in
        // exactly the case it exists to catch.
        state.deadline = Date.now() + config.graceSeconds * 1000;
        showReturnOverlay(state.deadline);
        tickCountdown();
        state.countdownTimer = window.setInterval(tickCountdown, 250);
    }

    function tickCountdown() {
        if (!state.deadline) return;
        var remaining = Math.max(0, state.deadline - Date.now());
        var seconds = Math.ceil(remaining / 1000);
        $overlayClock.text(fmt(gettext('%(s)s seconds remaining'), {s: seconds}));
        if (remaining <= 0) {
            stopCountdown();
            report('grace_expired', '', {force: true});
        }
    }

    function stopCountdown() {
        state.deadline = null;
        if (state.countdownTimer) {
            window.clearInterval(state.countdownTimer);
            state.countdownTimer = null;
        }
    }

    function showReturnOverlay(deadline) {
        hideArm();
        $overlay.find('.strict-body').text(
            deadline
                ? gettext('You left the proctored session. Return to fullscreen now, or you will be disqualified from this contest.')
                : gettext('You left the proctored session. This has been recorded. Return to fullscreen to continue.')
        );
        if (!deadline) $overlayClock.text('');
        updateOverlayCounts();
        $overlay.show();
    }

    /* ------------------------------------------------------------ heartbeat */

    function startHeartbeat() {
        if (state.heartbeatTimer || state.teardown) return;
        var interval = (config.heartbeatInterval || 15) * 1000;
        beat();
        state.heartbeatTimer = window.setInterval(beat, interval);
    }

    function beat() {
        if (state.teardown || state.banned) return;
        $.ajax({
            url: config.heartbeatUrl,
            type: 'POST',
            contentType: 'application/json',
            headers: {'X-CSRFToken': csrfToken()},
            data: JSON.stringify({fullscreen: !!fullscreenElement()}),
            dataType: 'json'
        }).done(applyState);
    }

    function teardown() {
        state.teardown = true;
        stopCountdown();
        if (state.heartbeatTimer) {
            window.clearInterval(state.heartbeatTimer);
            state.heartbeatTimer = null;
        }
        hideArm();
        hideOverlay();
    }

    /* ------------------------------------------------------- navigation gate */

    var navPendingTimer = null;

    function markNavigation() {
        state.navPending = true;
        try {
            window.sessionStorage.setItem(NAV_KEY, String(Date.now()));
        } catch (e) { /* private mode; the in-memory flag still covers this page */ }
        // A navigation that never happens (cancelled download, blocked submit)
        // must not leave monitoring disabled for the rest of the session.
        if (navPendingTimer) window.clearTimeout(navPendingTimer);
        navPendingTimer = window.setTimeout(function () {
            state.navPending = false;
        }, NAV_WINDOW_MS);
    }

    function arrivedFromContestNavigation() {
        var stamp;
        try {
            stamp = window.sessionStorage.getItem(NAV_KEY);
            window.sessionStorage.removeItem(NAV_KEY);
        } catch (e) {
            return false;
        }
        return !!stamp && (Date.now() - parseInt(stamp, 10)) < NAV_WINDOW_MS;
    }

    function isAllowedUrl(href) {
        if (!href) return false;
        var url;
        try {
            url = new URL(href, window.location.href);
        } catch (e) {
            return false;
        }
        if (url.origin !== window.location.origin) return false;
        var path = url.pathname;
        return path.indexOf('/contest/' + config.key) === 0 ||
            path.indexOf('/problem/') === 0 ||
            path.indexOf('/submission/') === 0 ||
            path.indexOf('/widgets/') === 0 ||
            path.indexOf('/static/') === 0 ||
            path.indexOf('/media/') === 0;
    }

    /* -------------------------------------------------------------- binding */

    function bindEvents() {
        // Fullscreen. Only a real transition out counts -- a page that simply
        // loads outside fullscreen is the normal state after any navigation.
        ['fullscreenchange', 'webkitfullscreenchange', 'mozfullscreenchange',
            'MSFullscreenChange'].forEach(function (name) {
            document.addEventListener(name, onFullscreenChange, false);
        });

        // The Page Visibility plumbing in common.js already normalises the
        // vendor prefixes and fires these; no second raw listener needed.
        $(window).on('dmoj:window-hidden', function () {
            reportFocusLoss('tab hidden');
        });
        $(window).on('blur', function () {
            reportFocusLoss('window blurred');
        });

        // Capture phase, so the editor never sees the event.
        document.addEventListener('paste', function (e) {
            if (state.teardown) return;
            e.preventDefault();
            e.stopPropagation();
            report('paste', '', {bucket: 'clipboard'});
            flashNotice(gettext('Pasting is disabled in this contest.'));
        }, true);

        document.addEventListener('cut', function (e) {
            if (state.teardown) return;
            e.preventDefault();
            e.stopPropagation();
            report('cut', '', {bucket: 'clipboard'});
        }, true);

        // Copy is recorded but allowed: contestants legitimately copy sample
        // input out of the statement, and blocking it also breaks screen
        // readers. It is not a counted violation server-side either.
        document.addEventListener('copy', function () {
            if (state.teardown) return;
            report('copy', '', {bucket: 'clipboard-copy'});
        }, true);

        document.addEventListener('contextmenu', function (e) {
            if (state.teardown) return;
            e.preventDefault();
            report('context_menu', '');
        }, false);

        document.addEventListener('keydown', onKeyDown, true);

        $(document).on('click', 'a[href]', function (e) {
            if (state.teardown) return;
            var href = $(this).attr('href');
            if (!href || href.charAt(0) === '#' || href.indexOf('javascript:') === 0) return;
            if ($(this).attr('target') === '_blank') {
                e.preventDefault();
                report('navigate_away', href);
                flashNotice(gettext('Opening other pages is disabled during this contest.'));
                return;
            }
            if (isAllowedUrl(href)) {
                markNavigation();
                return;
            }
            e.preventDefault();
            report('navigate_away', href);
            flashNotice(gettext('You cannot leave the contest while it is running.'));
        });

        // Submitting a solution is a navigation we caused.
        $(document).on('submit', 'form', markNavigation);

        window.addEventListener('beforeunload', function (e) {
            if (state.teardown || state.banned || state.navPending) return;
            report('navigate_away', 'unload', {keepalive: true, force: true});
            e.preventDefault();
            e.returnValue = '';
            return '';
        });

        bindAceGuards();
    }

    function onFullscreenChange() {
        if (state.teardown) return;
        if (fullscreenElement()) {
            stopCountdown();
            hideArm();
            hideOverlay();
            return;
        }
        if (state.navPending) return;   // our own page transition
        if (!state.armed) return;       // never started; nothing to leave
        report('fullscreen_exit', '', {bucket: 'focus'});
        startCountdown();
    }

    function reportFocusLoss(detail) {
        if (state.teardown || state.navPending) return;
        if (!state.armed) return;
        report('focus_lost', detail, {bucket: 'focus'});
    }

    function onKeyDown(e) {
        if (state.teardown) return;
        var ctrl = e.ctrlKey || e.metaKey;
        var key = (e.key || '').toLowerCase();

        if (ctrl && (key === 'p' || key === 's' || key === 'u')) {
            e.preventDefault();
            report('blocked_key', 'ctrl+' + key, {bucket: 'key'});
            return;
        }
        // F12 and ctrl+shift+i/j/c cannot actually be prevented in Chrome.
        // Recording them is honest; pretending to block them would not be.
        if (e.key === 'F12' || (ctrl && e.shiftKey && 'ijc'.indexOf(key) !== -1)) {
            report('blocked_key', 'devtools: ' + (e.key || key), {bucket: 'key'});
        }
    }

    function bindAceGuards() {
        function guard(editor) {
            if (!editor || editor._strictGuarded) return;
            editor._strictGuarded = true;
            editor.on('paste', function (ev) {
                ev.text = '';
            });
            editor.on('copy', function () { /* recorded by the document handler */ });
        }

        $(document).on('ace_load', '.django-ace-widget', function (e, editor) {
            guard(editor);
        });
        $(window).on('load', function () {
            guard(window.ace_source);
        });
        if (window.ace_source) guard(window.ace_source);
    }

    var noticeTimer = null;

    function flashNotice(text) {
        var $notice = $('#strict-notice');
        if (!$notice.length) {
            $notice = $('<div id="strict-notice"></div>').appendTo(document.body);
        }
        $notice.text(text).show();
        if (noticeTimer) window.clearTimeout(noticeTimer);
        noticeTimer = window.setTimeout(function () { $notice.fadeOut(200); }, 3000);
    }

    /* ----------------------------------------------------------------- boot */

    $(function () {
        buildUI();

        if (!fullscreenSupported()) {
            // Chiefly iPhone Safari, which has no Fullscreen API at all. Say so
            // rather than silently letting them compete unproctored.
            showArm(
                gettext('This contest requires fullscreen, which this browser does not support.'),
                gettext('Use a computer, an Android phone, or an iPad. iPhone is not supported.')
            );
            return;
        }

        state.navPending = false;
        var cameFromNavigation = arrivedFromContestNavigation();

        bindEvents();

        if (fullscreenElement()) {
            if (state.armed) {
                startHeartbeat();
            } else {
                showArm(gettext('Click below to start your proctored session. You cannot submit until you do.'));
            }
            return;
        }

        // Not in fullscreen. If we just navigated within the contest, that is
        // our own doing and costs nothing -- just ask them to re-enter. If they
        // arrived any other way, the same panel blocks the page until they arm.
        showArm(
            state.armed && !cameFromNavigation
                ? gettext('Your proctored session is not in fullscreen. Return to fullscreen to continue.')
                : gettext('Click below to start your proctored session. You cannot submit until you do.')
        );
        if (state.armed) startHeartbeat();
    });
})();
