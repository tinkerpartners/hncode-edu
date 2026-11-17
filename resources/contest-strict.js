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

    // A18: never inside a frame. The IDE used to submit into a hidden
    // same-origin iframe, and the page that landed there was the IDE again --
    // with this very script. Running there it reported the iframe losing focus
    // (the contestant clicking back into the real page) as "window blurred", a
    // counted violation for doing nothing; with the exam limit of 1, the first
    // submission locked them out. Any frame still must not run it.
    if (window.top !== window.self) return;

    var configNode = document.getElementById('strict-contest-config');
    if (!configNode) return;

    var config;
    try {
        config = JSON.parse(configNode.textContent);
    } catch (e) {
        return;
    }
    if (!config || !config.eventUrl) return;

    var NAV_WINDOW_MS = 8000;
    var CLIENT_DEDUPE_MS = 2000;

    var state = {
        violations: config.violations || 0,
        limit: config.limit,
        armed: !!config.armed,
        arming: false,
        banned: false,
        locked: false,
        deadline: null,
        countdownTimer: null,
        heartbeatTimer: null,
        lastReport: {},
        focusLost: false,
        violationNotice: false,
        navPending: false,
        navigating: false,
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
            document.body.classList.remove('strict-contest-session');
            return;
        }
        if (typeof data.violations === 'number' && data.violations > state.violations) {
            state.violations = data.violations;
            state.violationNotice = true;
        }
        if (typeof data.limit === 'number') state.limit = data.limit;
        updateOverlayCounts();
        if (data.banned) {
            showBanned(data.redirect || config.contestUrl);
        } else if (data.locked) {
            showLocked();
        } else {
            showViolationNotice();
        }
    }

    /*
     * A18: locked out until an administrator unlocks them. Like showBanned, a
     * terminal panel rather than a redirect; unlike it, the panel keeps asking
     * the server, and reloads the page as soon as the lock is lifted so the
     * contestant re-arms fullscreen and carries on. Nothing is reported while
     * locked: the server stops counting anyway.
     */
    var LOCK_POLL_MS = 15000;
    var lockPollTimer = null;

    function showLocked() {
        if (state.locked) return;
        state.locked = true;
        teardown();
        state.locked = true;
        if (document.exitFullscreen && fullscreenElement()) {
            try {
                document.exitFullscreen();
            } catch (e) { /* nothing to do */ }
        }
        $arm.find('.strict-title').text(gettext('You are locked out of this contest'));
        $arm.find('.strict-body').text(
            gettext('You left the proctored session, so the contest is locked for you and the administrator has been notified. Your work so far is kept. Wait here: this page continues by itself once the administrator unlocks you.')
        );
        $arm.find('.strict-note').text('');
        $arm.find('.strict-button')
            .off('click')
            .text(gettext('Check again'))
            .on('click', pollUnlock);
        hideOverlay();
        $arm.show();
        if (!lockPollTimer) lockPollTimer = window.setInterval(pollUnlock, LOCK_POLL_MS);
    }

    function pollUnlock() {
        $.ajax({
            url: config.heartbeatUrl,
            type: 'POST',
            contentType: 'application/json',
            headers: {'X-CSRFToken': csrfToken()},
            data: JSON.stringify({fullscreen: false}),
            dataType: 'json'
        }).done(function (data) {
            if (data && data.ok !== false && !data.locked && !data.banned) {
                window.clearInterval(lockPollTimer);
                lockPollTimer = null;
                window.location.reload();
            } else if (data && data.banned) {
                window.clearInterval(lockPollTimer);
                lockPollTimer = null;
                state.banned = false;
                showBanned(data.redirect || config.contestUrl);
            }
        });
        // Network errors: keep waiting, the next tick tries again.
    }

    function showBanned(href) {
        if (state.banned) return;
        state.banned = true;
        // Deliberately NOT a redirect. If the destination still loads this
        // script -- which it does whenever current_contest points at a
        // disqualified participation -- redirecting here bounces the page
        // forever, which is what "the screen keeps opening and closing" was.
        // A terminal panel cannot loop no matter what state the server is in.
        teardown();
        state.banned = true;
        if (document.exitFullscreen && fullscreenElement()) {
            try {
                document.exitFullscreen();
            } catch (e) { /* nothing to do */ }
        }
        $arm.find('.strict-title').text(gettext('You have been disqualified'));
        $arm.find('.strict-body').text(
            gettext('You have been disqualified from this contest and cannot rejoin. Contact the contest administrator if you believe this was a mistake.')
        );
        $arm.find('.strict-note').text('');
        $arm.find('.strict-button')
            .off('click')
            .text(gettext('Back to the contest page'))
            .on('click', function () {
                window.location.href = href;
            });
        hideOverlay();
        $arm.show();
    }

    function report(action, detail, options) {
        options = options || {};
        if (state.teardown || state.banned || state.locked) return;

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

        function send(attempt) {
            var request = $.ajax({
                url: config.eventUrl,
                type: 'POST',
                contentType: 'application/json',
                headers: {'X-CSRFToken': csrfToken()},
                data: body,
                dataType: 'json',
                timeout: 8000
            }).done(applyState);
            if (options.retry && attempt < 2) {
                request.fail(function (_, status) {
                    if (status !== 'abort' && !state.teardown) {
                        window.setTimeout(function () {
                            if (!state.teardown) send(attempt + 1);
                        }, 1000 * (attempt + 1));
                    }
                });
            }
            return request;
        }
        return send(0);
    }

    /* ------------------------------------------------------------------- UI */

    var $arm, $overlay, $overlayCount, $overlayClock, $status, $violationAlert;
    var violationAlertTimer = null;

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
        $status = $('<div id="strict-session-status"><span></span> <strong></strong></div>')
            .appendTo(document.body).hide();
        $status.find('span').text(gettext('Number of violations'));
        $violationAlert = $(
            '<div id="strict-violation-alert" role="alert">' +
            '<i class="fa fa-exclamation-triangle strict-violation-icon" aria-hidden="true"></i>' +
            '<strong class="strict-violation-title"></strong>' +
            '<span class="strict-violation-label"></span>' +
            '<strong class="strict-violation-total"></strong>' +
            '</div>'
        ).appendTo(document.body).hide();
        $violationAlert.find('.strict-violation-title').text(gettext('Violation recorded'));
        $violationAlert.find('.strict-violation-label').text(gettext('Number of violations'));

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
        var count = violationCount();
        $overlayCount.text(fmt(gettext('Number of violations %(n)s'), {n: count}));
        $status.find('strong').text(count);
        $status.toggle(state.armed && !state.teardown && !state.locked && !state.banned);
    }

    function violationCount() {
        return config.autoban ? state.violations + ' / ' + state.limit : String(state.violations);
    }

    function showViolationNotice() {
        if (!state.violationNotice || state.focusLost || document.hidden) return;
        state.violationNotice = false;
        $violationAlert.find('.strict-violation-total').text(violationCount());
        $violationAlert.show();
        window.clearTimeout(violationAlertTimer);
        violationAlertTimer = window.setTimeout(function () {
            $violationAlert.hide();
        }, 5000);
    }

    function hideOverlay() {
        if ($overlay) $overlay.hide();
    }

    /* -------------------------------------------------------------- arming */

    function onArmClick() {
        if (state.arming) return;
        state.arming = true;
        var $buttons = $arm.add($overlay).find('.strict-button').prop('disabled', true);
        var ready = fullscreenElement() ? Promise.resolve() : requestFullscreen();
        ready.then(function () {
            stopCountdown();
            return report(state.armed ? 'returned' : 'session_start', '', {force: true});
        }).then(function (data) {
            if (state.teardown) return;
            if (!data || !data.armed) throw new Error('session not armed');
            state.armed = true;
            resumeFocus();
            hideArm();
            hideOverlay();
            updateOverlayCounts();
            startHeartbeat();
            if (!fullscreenElement()) onFullscreenChange();
        }).catch(function () {
            if (state.teardown) return;
            showArm(fullscreenElement()
                ? gettext('Could not confirm the proctored session. Try again.')
                : gettext('Your browser refused to enter fullscreen. Check that fullscreen is allowed for this site, then try again.'));
        }).finally(function () {
            state.arming = false;
            $buttons.prop('disabled', false);
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
                ? gettext('You left the proctored session. Return to fullscreen now, or the contest will be locked for you.')
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
        var focused = !document.hidden && document.hasFocus();
        if (focused) resumeFocus();
        else reportFocusLoss(document.hidden ? 'tab hidden' : 'window blurred');
        $.ajax({
            url: config.heartbeatUrl,
            type: 'POST',
            contentType: 'application/json',
            headers: {'X-CSRFToken': csrfToken()},
            data: JSON.stringify({fullscreen: !!fullscreenElement() && focused}),
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
        if ($status) $status.hide();
        if ($violationAlert) $violationAlert.hide();
        window.clearTimeout(violationAlertTimer);
    }

    /* ------------------------------------------------------- navigation gate */

    var navPendingTimer = null;

    function markNavigation() {
        state.navPending = true;
        // A navigation that never happens (cancelled download, blocked submit)
        // must not leave monitoring disabled for the rest of the session.
        if (navPendingTimer) window.clearTimeout(navPendingTimer);
        navPendingTimer = window.setTimeout(function () {
            state.navPending = false;
            state.navigating = false;
        }, NAV_WINDOW_MS);
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
        // A18: this contest's quizzes are part of the contest. Leaving for one
        // is an announced navigation, not an escape; the quiz page asks to
        // re-enter fullscreen and is itself proctored.
        var quizCodes = config.quizCodes || [];
        for (var i = 0; i < quizCodes.length; i++) {
            var quizPath = '/quiz/' + encodeURIComponent(quizCodes[i]);
            if (path === quizPath || path.indexOf(quizPath + '/') === 0) return true;
        }
        var contestPath = '/contest/' + encodeURIComponent(config.key);
        // The statement, IDE, submit page and the submission lists of this
        // contest's problems stay inside the contest: the problem list itself
        // links /rank/, and a counted violation for clicking it would be a
        // false positive. Editorials/solutions stay out of scope.
        var problem = /^\/problem\/([^/]+)(?:\/(?:ide|submit|rank|submissions)(?:\/.*)?)?\/?$/.exec(path);
        return path === contestPath || path.indexOf(contestPath + '/') === 0 ||
            (!!problem && (config.problemCodes || []).some(function (code) {
                return encodeURIComponent(code) === problem[1];
            })) ||
            path.indexOf('/submission/') === 0 ||
            path.indexOf('/widgets/') === 0 ||
            path.indexOf('/static/') === 0 ||
            path.indexOf('/media/') === 0;
    }

    /* -------------------------------------------------------------- binding */

    function isPageLink(href) {
        return href && href.charAt(0) !== '#' && !/^javascript:/i.test(href);
    }

    function isContestScreen(href) {
        if (!isAllowedUrl(href)) return false;
        return /^\/(contest|problem|quiz|submission)\//.test(new URL(href, window.location.href).pathname);
    }

    function guardLinkClick(e) {
        if (state.teardown || e.defaultPrevented || (e.type === 'auxclick' && e.button !== 1)) return;
        var link = e.target.closest && e.target.closest('a[href]');
        if (!link) return;
        var href = link.getAttribute('href');
        if (!isPageLink(href)) return;

        // Capture before the general AJAX router can fetch an unauthorized
        // destination. Modifier clicks and non-self targets open another context.
        var target = (link.getAttribute('target') || '').toLowerCase();
        var newContext = (target && target !== '_self') || e.type === 'auxclick' ||
            e.ctrlKey || e.metaKey || e.shiftKey || e.altKey;
        if (!newContext && isAllowedUrl(href)) {
            if (isContestScreen(href)) {
                e.preventDefault();
                e.stopImmediatePropagation();
                openContestScreen(href);
            }
            return;
        }
        e.preventDefault();
        e.stopImmediatePropagation();
        if (state.armed) report('navigate_away', href);
        flashNotice(newContext
            ? gettext('Opening other pages is disabled during this contest.')
            : gettext('You cannot leave the contest while it is running.'));
    }

    function bindEvents() {
        // The general AJAX router only calls this when it needs a full load.
        window.hnStrictNavigate = function (url) {
            if (state.teardown) return true;
            if (!isAllowedUrl(url)) return false;
            markNavigation();
            return true;
        };
        window.hnStrictHistory = function () {
            if (state.teardown) return false;
            if (isContestScreen(window.location.href)) openContestScreen(window.location.href, true);
            return true;
        };
        // Fullscreen. Only a real transition out counts -- a page that simply
        // loads outside fullscreen is the normal state after any navigation.
        ['fullscreenchange', 'webkitfullscreenchange', 'mozfullscreenchange',
            'MSFullscreenChange'].forEach(function (name) {
            document.addEventListener(name, onFullscreenChange, false);
        });

        document.addEventListener('visibilitychange', function () {
            if (document.hidden) reportFocusLoss('tab hidden');
            else resumeFocus();
        });
        window.addEventListener('blur', function () {
            var active = document.activeElement;
            if (active && active.tagName === 'IFRAME' && document.hasFocus()) return;
            reportFocusLoss('window blurred');
        });
        window.addEventListener('focus', resumeFocus);

        // Capture phase, so the editor never sees the event.
        document.addEventListener('paste', function (e) {
            if (state.teardown || config.allowPaste) return;
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

        document.addEventListener('copy', function (e) {
            if (state.teardown || config.allowCopy !== false) return;
            e.preventDefault();
            e.stopPropagation();
            flashNotice(gettext('Copying is disabled in this contest.'));
            report('copy', '', {bucket: 'clipboard-copy'});
        }, true);

        document.addEventListener('contextmenu', function (e) {
            if (state.teardown) return;
            e.preventDefault();
            report('context_menu', '');
        }, false);

        document.addEventListener('keydown', onKeyDown, true);
        document.addEventListener('click', guardLinkClick, true);
        document.addEventListener('auxclick', guardLinkClick, true);

        $(document).on('click', 'a[href]', function (e) {
            if (state.teardown || e.isDefaultPrevented()) return;
            var href = $(this).attr('href');
            if (!isPageLink(href)) return;
            if (isAllowedUrl(href)) {
                markNavigation();
            }
        });

        // Submitting a solution is a navigation we caused -- unless the form
        // posts with fetch() (data-async: the IDE), which navigates nowhere;
        // marking it would blind the monitoring for the next few seconds.
        $(document).on('submit', 'form', function (e) {
            if (state.teardown || e.isDefaultPrevented() || this.hasAttribute('data-async')) return;
            var action = e.originalEvent && e.originalEvent.submitter;
            action = (action && action.getAttribute('formaction')) || this.action;
            if (isAllowedUrl(action)) markNavigation();
        });

        window.addEventListener('beforeunload', function (e) {
            if (state.teardown || state.banned) return;
            if (state.navPending) {
                state.navigating = true;
                return;
            }
            if (!state.armed) return;
            report('navigate_away', 'unload', {keepalive: true, force: true});
            e.preventDefault();
            e.returnValue = '';
            return '';
        });

        try {
            bindAceGuards();
        } catch (e) {
            if (window.console) console.error('strict: ace guards failed', e);
        }
    }

    function onFullscreenChange() {
        if (state.teardown) return;
        if (fullscreenElement()) {
            stopCountdown();
            if (state.armed && !state.arming) {
                hideArm();
                hideOverlay();
            }
            return;
        }
        if (state.navigating) return;
        if (!state.armed) return;       // never started; nothing to leave
        report('fullscreen_exit', '', {bucket: 'focus', retry: true});
        startCountdown();
    }

    function reportFocusLoss(detail) {
        if (state.teardown || state.navigating || state.focusLost || !state.armed) return;
        state.focusLost = true;
        report('focus_lost', detail, {bucket: 'focus', retry: true});
    }

    function resumeFocus() {
        if (state.teardown || !state.focusLost || document.hidden || !document.hasFocus()) return;
        state.focusLost = false;
        showViolationNotice();
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
        if (config.allowPaste) return;
        function guard(editor) {
            // `window.ace_source` is the *div* with that id until django_ace's
            // window-load handler replaces it with the editor -- browsers expose
            // element ids as window properties. Duck-type instead of trusting it.
            if (!editor || typeof editor.on !== 'function') return;
            if (editor._strictGuarded) return;
            editor._strictGuarded = true;
            editor.on('paste', function (ev) {
                if (!state.teardown) ev.text = '';
            });
        }

        $(document).on('ace_load', '.django-ace-widget', function (e, editor) {
            guard(editor);
        });
        $(window).on('load', function () {
            guard(window.ace_source);
        });
        if (window.ace_source) guard(window.ace_source);
    }

    /* ----------------------------------------------- in-page contest views */

    var idePrefix = '/contest/' + config.key + '/ide/';

    function onIDEPage() {
        return !!document.querySelector('#hn-ide.is-contest');
    }

    function isIDEUrl(href) {
        try {
            var url = new URL(href, window.location.href);
            return url.origin === window.location.origin &&
                url.pathname.indexOf(idePrefix) === 0;
        } catch (e) {
            return false;
        }
    }

    function acceptContestScreen(destination, $page) {
        if (!isContestScreen(destination)) return false;
        var node = $page.find('#strict-contest-config')[0];
        try {
            return !!node && JSON.parse(node.textContent).key === config.key;
        } catch (e) {
            return false;
        }
    }

    var screenGeneration = 0;
    var screenUrl = window.location.href;

    function openContestScreen(href, fromHistory) {
        var generation = ++screenGeneration;
        function failed() {
            if (generation !== screenGeneration) return;
            if (fromHistory) window.history.replaceState(null, '', screenUrl);
            flashNotice(gettext('Could not open that contest screen. Try again.'));
        }
        if (!fromHistory && !window.currentRequest && onIDEPage() && isIDEUrl(href) && window.HNIDE) {
            window.HNIDE.switchTo(href).then(function (applied) {
                if (!applied || generation !== screenGeneration) return;
                window.history.replaceState(null, '', href);
                screenUrl = window.location.href;
            }).catch(failed);
            return;
        }
        navigateTo(href, '#content', false, {
            fromHistory: fromHistory,
            accepts: acceptContestScreen,
            onError: failed,
            onReady: function () { screenUrl = window.location.href; }
        });
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

        if (config.locked) {
            // Locked before this page loaded: wait for the unlock, nothing else.
            showLocked();
            return;
        }

        if (!fullscreenSupported()) {
            // Chiefly iPhone Safari, which has no Fullscreen API at all. Say so
            // rather than silently letting them compete unproctored.
            showArm(
                gettext('This contest requires fullscreen, which this browser does not support.'),
                gettext('Use a computer, an Android phone, or an iPad. iPhone is not supported.')
            );
            return;
        }

        bindEvents();

        if (fullscreenElement()) {
            if (state.armed) {
                updateOverlayCounts();
                startHeartbeat();
            } else {
                showArm(gettext('Click below to start your proctored session. You cannot submit until you do.'));
            }
            return;
        }

        if (state.armed) {
            $arm.find('.strict-button').text(gettext('Return to fullscreen'));
            updateOverlayCounts();
        }
        showArm(
            state.armed
                ? gettext('Your proctored session is not in fullscreen. Return to fullscreen to continue.')
                : gettext('Click below to start your proctored session. You cannot submit until you do.')
        );
        if (state.armed) startHeartbeat();
    });
})();
