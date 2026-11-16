const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const {sourceFunction} = require('./helpers/source.cjs');
const functionSource = name => sourceFunction('resources/ide.js', name);

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

function ide() {
    const requests = [], renders = [], errors = [], timers = new Map();
    let nextTimer = 0;
    const cfg = {problem: 'first', problemId: 1, draftScope: 'user:1:first', runEnabled: true, runUrl: '/first/run'};
    const root = {isConnected: true, querySelector: () => null, setAttribute() {}};
    const runBtn = {disabled: false};
    const window = {
        fetch(url, options) {
            const result = deferred();
            requests.push({url, options, ...result});
            return result.promise;
        },
        setTimeout(fn) { const id = ++nextTimer; timers.set(id, fn); return id; },
        clearTimeout(id) { timers.delete(id); },
    };
    const document = {title: '', getElementById: () => null};
    const context = vm.createContext({
        window, document, cfg, root, runBtn, runResult: null, hiddenSubmit: null,
        currentRun: null, runTimer: null, runGeneration: 0, switchGeneration: 0,
        detailPanel: null, detailTrigger: null,
        activeCase: 0, shownCase: 0, cases: [{input: '1 2', expected: '3'}],
        T: {network: 'network error'},
        FormData: class { append() {} },
        DOMParser: class {
            parseFromString(html) {
                return {
                    title: html,
                    getElementById(id) {
                        return id === 'hn-ide' ? {getAttribute: () => html} : null;
                    },
                    querySelector: () => null,
                };
            }
        },
        $: () => ({val: () => 1, trigger() {}}), $id: () => null,
        isFileOnly: () => false, readCaseFields() {}, saveCases() {},
        csrfToken: () => 'csrf', ioMode: () => 'std',
        editor: () => ({getSession: () => ({getValue: () => 'code'})}),
        setRunBusy(value) { runBtn.disabled = value; },
        showConsoleTab() {}, revealConsoleForAction() {}, showLeftTab() {},
        renderRun(payload) { renders.push({problem: cfg.problem, payload}); },
        runError(message) { runBtn.disabled = false; errors.push(message); },
        swapLanguages() {}, loadCases() {}, loadIo() {}, renderRecent() {},
    });
    for (const name of ['isCurrentRun', 'invalidateRun', 'pollRun', 'runCode', 'casesKey', 'ioKey',
        'readProblemConfig', 'applyProblemConfig', 'replaceInner', 'closeSubmissionDetails', 'switchTo']) {
        vm.runInContext(functionSource(name), context);
    }
    function reply(request, payload) {
        request.resolve({ok: true, headers: {get: () => 'application/json'}, json: () => Promise.resolve(payload)});
    }
    function changeProblem() {
        context.invalidateRun();
        cfg.problem = 'second'; cfg.draftScope = 'user:1:second'; cfg.runUrl = '/second/run';
    }
    return {context, cfg, root, runBtn, requests, renders, errors, timers, reply, changeProblem};
}

test('late Run creation cannot render on the next problem or unlock its Run button', async () => {
    const h = ide();
    h.context.runCode();
    h.changeProblem(); h.context.runCode();
    h.reply(h.requests[0], {ok: true, id: 1, finished: true});
    await flush();
    assert.equal(h.renders.some(row => row.payload.id === 1), false);
    assert.equal(h.runBtn.disabled, true);
    h.reply(h.requests[1], {ok: true, id: 2, finished: true});
    await flush();
    assert.equal(h.renders.at(-1).problem, 'second');
    assert.equal(h.renders.at(-1).payload.id, 2);
    assert.equal(h.runBtn.disabled, false);
});

test('switching after Run headers arrive but before JSON decoding also discards the old result', async () => {
    const h = ide(), body = deferred();
    h.context.runCode();
    h.requests[0].resolve({ok: true, headers: {get: () => 'application/json'}, json: () => body.promise});
    await flush();
    h.changeProblem();
    body.resolve({ok: true, id: 9, finished: false});
    await flush();
    assert.equal(h.context.currentRun, null);
    assert.equal(h.renders.some(row => row.payload.id === 9), false);
    assert.equal(h.timers.size, 0);
});

test('a late Run network error cannot replace the next problem result', async () => {
    const h = ide();
    h.context.runCode(); h.changeProblem(); h.context.runCode();
    h.requests[0].reject(new Error('offline'));
    await flush();
    assert.equal(h.errors.length, 0);
    assert.equal(h.runBtn.disabled, true);
});

test('malformed JSON from the active Run ends busy state and reports a network error', async () => {
    const h = ide();
    h.context.runCode();
    h.requests[0].resolve({ok: true, headers: {get: () => 'application/json'}, json: () => Promise.reject(new Error('bad JSON'))});
    await flush();
    assert.deepEqual(h.errors, ['network error']);
    assert.equal(h.runBtn.disabled, false);
});

test('late polling success and error after a problem change cannot render or schedule more polls', async () => {
    for (const failure of [false, true]) {
        const h = ide();
        h.context.runCode();
        h.reply(h.requests[0], {ok: true, id: 1, url: '/run/1', finished: false});
        await flush();
        const timer = h.timers.values().next().value;
        h.timers.clear(); timer();
        assert.equal(h.requests.length, 2);
        h.changeProblem();
        const before = h.renders.length;
        if (failure) h.requests[1].reject(new Error('offline'));
        else h.reply(h.requests[1], {id: 1, finished: true});
        await flush();
        assert.equal(h.renders.length, before);
        assert.equal(h.errors.length, 0);
        assert.equal(h.timers.size, 0);
    }
});

test('Run responses cannot mutate an IDE that was removed by normal AJAX navigation', async () => {
    const h = ide();
    h.context.runCode(); h.root.isConnected = false;
    h.reply(h.requests[0], {ok: true, id: 1, finished: true});
    await flush();
    assert.equal(h.renders.length, 1); // Initial queued state only.
});

test('a stale polling call cannot cancel the current run timer', () => {
    const h = ide();
    h.context.currentRun = 2;
    h.context.runGeneration = 2;
    h.context.runTimer = 99;
    h.timers.set(99, () => {});
    h.context.pollRun({id: 1}, Date.now(), 1);
    assert.equal(h.timers.has(99), true);
});

test('custom cases and I/O storage are separate for different users and contest contexts', () => {
    const h = ide();
    const first = [h.context.casesKey(), h.context.ioKey()];
    h.cfg.draftScope = 'user:2:first';
    const second = [h.context.casesKey(), h.context.ioKey()];
    h.cfg.draftScope = 'user:1:contest:9:first';
    const contest = [h.context.casesKey(), h.context.ioKey()];
    assert.notDeepEqual(first, second);
    assert.notDeepEqual(first, contest);
    assert.ok(!first.includes('hn-ide:cases:first'));
    assert.ok(!first.includes('hn-ide:io:first'));
});

test('in-place IDE navigation applies only the latest requested problem', async () => {
    const h = ide();
    const first = h.context.switchTo('/first'), second = h.context.switchTo('/second');
    h.requests[1].resolve({ok: true, text: () => Promise.resolve(JSON.stringify({problem: 'second', draftScope: 'user:1:second'}))});
    assert.equal(await second, true);
    h.requests[0].resolve({ok: true, text: () => Promise.resolve(JSON.stringify({problem: 'first', draftScope: 'user:1:first'}))});
    assert.equal(await first, false);
    assert.equal(h.cfg.problem, 'second');
});

test('a stale IDE switch failure cannot trigger fallback navigation over a newer switch', async () => {
    const h = ide();
    const first = h.context.switchTo('/first'), second = h.context.switchTo('/second');
    h.requests[0].reject(new Error('old request failed'));
    assert.equal(await first, false);
    h.requests[1].resolve({ok: true, text: () => Promise.resolve(JSON.stringify({problem: 'second'}))});
    assert.equal(await second, true);
});

test('the source textarea pairs brackets, indents a block and handles Tab', () => {
    const sourceInput = {
        value: '', selectionStart: 0, selectionEnd: 0,
        setRangeText(text, start, end) {
            this.value = this.value.slice(0, start) + text + this.value.slice(end);
        },
        setSelectionRange(start, end) {
            this.selectionStart = start;
            this.selectionEnd = end;
        },
        dispatchEvent() {},
    };
    const context = vm.createContext({
        sourceInput, Event,
        document: {querySelector: () => null},
    });
    vm.runInContext(functionSource('onFallbackKeydown'), context);
    function key(name) {
        let handled = false;
        context.onFallbackKeydown({key: name, preventDefault() { handled = true; }});
        assert.equal(handled, true);
    }
    key('{');
    assert.equal(sourceInput.value, '{}');
    assert.equal(sourceInput.selectionStart, 1);
    key('Enter');
    assert.equal(sourceInput.value, '{\n    \n}');
    assert.equal(sourceInput.selectionStart, 6);
    key('Tab');
    assert.equal(sourceInput.value, '{\n        \n}');
    assert.equal(sourceInput.selectionStart, 10);
    sourceInput.setSelectionRange(sourceInput.value.length - 1, sourceInput.value.length - 1);
    key('}');
    assert.equal(sourceInput.selectionStart, sourceInput.value.length);
});

test('a Strict result updates the details pane without navigating', async () => {
    const detailPanel = {hidden: true}, detailContent = {}, detailTitle = {focus() {}};
    const requests = [];
    const context = vm.createContext({
        detailPanel, detailContent, detailTitle, detailRequest: null, detailId: null,
        root: {isConnected: true}, detailTrigger: null,
        cfg: {submissionDetailUrl: '/widgets/submission_testcases'},
        T: {loading: 'Loading', submissionDetails: 'Submission', unavailableDetails: 'Unavailable'},
        fmt: (label, {id}) => label + ' #' + id,
        showConsoleTab() {}, revealConsoleForAction() {}, AbortController, URL,
        window: {
            location: {origin: 'http://testserver', href: 'http://testserver/contest/test/ide/a'},
            fetch(url, options) {
                const request = deferred();
                requests.push({url, options, ...request});
                return request.promise;
            },
        },
    });
    for (const name of ['loadSubmissionDetails', 'openSubmissionDetails']) {
        vm.runInContext(functionSource(name), context);
    }
    context.openSubmissionDetails(1);
    context.openSubmissionDetails(2);
    assert.equal(detailPanel.hidden, false);
    assert.equal(detailTitle.textContent, 'Submission #2');
    assert.equal(requests[0].options.signal.aborted, true);
    assert.equal(requests[1].url.searchParams.get('id'), '2');
    requests[1].resolve({ok: true, text: () => Promise.resolve('Current result')});
    requests[0].resolve({ok: true, text: () => Promise.resolve('Stale result')});
    await flush();
    assert.equal(detailContent.innerHTML, 'Current result');
    assert.equal(context.window.location.href, 'http://testserver/contest/test/ide/a');
});

test('Strict copy and paste honor independent settings and only log blocked actions', () => {
    const actions = new Map(), reported = [];
    const context = vm.createContext({
        document: {addEventListener(name, listener) { actions.set(name, listener); }},
        window: {addEventListener() {}},
        $: () => ({on() {}}),
        state: {}, config: {allowCopy: true, allowPaste: false},
        gettext: msg => msg, flashNotice() {}, report: action => reported.push(action),
        onFullscreenChange() {}, onKeyDown() {}, bindAceGuards() {}, bindIDERouting() {}, guardLinkClick() {},
    });
    vm.runInContext(sourceFunction('resources/contest-strict.js', 'bindEvents'), context);
    context.bindEvents();
    function blocked(action) {
        let prevented = false;
        actions.get(action)({preventDefault() { prevented = true; }, stopPropagation() {}});
        return prevented;
    }
    assert.equal(blocked('copy'), false);
    assert.equal(blocked('paste'), true);
    context.config.allowCopy = false;
    context.config.allowPaste = true;
    assert.equal(blocked('copy'), true);
    assert.equal(blocked('paste'), false);
    assert.equal(blocked('cut'), true);
    assert.deepEqual(reported, ['paste', 'copy', 'cut']);
});

test('Strict only opens linked problems and quizzes from this contest in the current tab', () => {
    const reported = [];
    const context = vm.createContext({
        URL, config: {key: 'exam', problemCodes: ['a'], quizCodes: ['quiz1']},
        window: {location: new URL('https://example.test/contest/exam')},
        state: {armed: true}, flashNotice() {}, gettext: msg => msg,
        report: action => reported.push(action),
    });
    for (const name of ['isAllowedUrl', 'isPageLink', 'guardLinkClick']) {
        vm.runInContext(sourceFunction('resources/contest-strict.js', name), context);
    }
    function blocked(href, extras = {}, target = '') {
        let prevented = false;
        const link = {getAttribute: name => name === 'href' ? href : target};
        context.guardLinkClick({
            type: 'click', target: {closest: () => link},
            preventDefault() { prevented = true; }, stopImmediatePropagation() {}, ...extras,
        });
        return prevented;
    }
    assert.equal(blocked('/contest/exam/ide/a'), false);
    assert.equal(blocked('/problem/a'), false);
    assert.equal(blocked('/quiz/quiz1/take/1/'), false);
    assert.equal(blocked('/contest/exam-other/ide/a'), true);
    assert.equal(blocked('/problem/b'), true);
    assert.equal(blocked('/problem/a/solution'), true);
    assert.equal(blocked('/contest/exam/ide/a', {ctrlKey: true}), true);
    assert.equal(blocked('/contest/exam/ide/a', {type: 'auxclick', button: 1}), true);
    assert.equal(blocked('/contest/exam/ide/a', {}, '_blank'), true);
    assert.equal(reported.length, 6);
});

test('Strict still records lost focus while an approved navigation is pending', () => {
    const events = [];
    const state = {armed: true, navPending: true, navigating: false};
    const context = vm.createContext({state, report: (action, detail) => events.push([action, detail])});
    vm.runInContext(sourceFunction('resources/contest-strict.js', 'reportFocusLoss'), context);
    context.reportFocusLoss('window blurred');
    state.navigating = true;
    context.reportFocusLoss('document unloading');
    assert.deepEqual(events, [['focus_lost', 'window blurred']]);
});
