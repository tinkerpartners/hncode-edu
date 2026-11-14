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
        'readProblemConfig', 'applyProblemConfig', 'replaceInner', 'switchTo']) {
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
