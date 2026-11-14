const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
function sourceFunction(file, name) {
    const source = readFileSync(path.join(root, file), 'utf8');
    const match = source.match(new RegExp(`^function ${name}\\([^]*?^}\\r?$`, 'm')) ||
        source.match(new RegExp(`^  function ${name}\\([^]*?^  }\\r?$`, 'm'));
    assert.ok(match, `Missing source function ${name}`);
    return match[0];
}

// A deterministic transport fake: aborted requests can still deliver late
// callbacks, as a network race would. The actual application functions run in VM.
function transport(requests, events) {
    return function ajax(options) {
        const request = {
            options, aborted: false,
            abort() {
                this.aborted = true;
                events.push('abort');
                options.error?.(this, 'abort');
                options.complete?.();
            },
        };
        requests.push(request);
        return request;
    };
}

function navigation() {
    const requests = [], events = [], attrs = new Map();
    const window = {location: {href: '/original'}, flushSubmitDraft: () => events.push('flush')};
    const element = {
        length: 1,
        off() { return this; },
        attr(name, value) { attrs.set(name, value); return this; },
        removeAttr(name) { attrs.delete(name); return this; },
    };
    const $ = value => {
        if (typeof value === 'string' && value.startsWith('<')) {
            throw new Error('A stale success response reached DOM parsing');
        }
        return element;
    };
    $.ajax = transport(requests, events);
    const context = vm.createContext({window, document: {querySelector: () => null}, $});
    vm.runInContext(sourceFunction('resources/common.js', 'replaceLoadingPage') + '\n' +
        sourceFunction('resources/common.js', 'navigateTo'), context);
    return {requests, events, attrs, window, navigate: context.navigateTo};
}

test('navigation flushes the draft before aborting the previous request', () => {
    const h = navigation();
    h.navigate('/first');
    h.navigate('/second');
    assert.deepEqual(h.events, ['flush', 'flush', 'abort']);
    assert.equal(h.requests[0].aborted, true);
    assert.equal(h.window.currentRequest, h.requests[1]);
    assert.equal(h.attrs.get('aria-busy'), 'true');
});

test('late navigation success, error and completion cannot replace the latest request', () => {
    const h = navigation();
    h.navigate('/first');
    h.navigate('/second');
    const stale = h.requests[0].options;
    stale.success('<html>stale</html>');
    stale.error({}, 'error');
    stale.complete();
    assert.equal(h.window.location.href, '/original');
    assert.equal(h.window.currentRequest, h.requests[1]);
    assert.equal(h.attrs.get('aria-busy'), 'true');
});

test('the current navigation failure falls back to a normal page load', () => {
    const h = navigation();
    h.navigate('/destination');
    h.requests[0].options.error({}, 'error');
    assert.equal(h.window.location.href, '/destination');
    h.requests[0].options.complete();
    assert.equal(h.window.currentRequest, null);
    assert.equal(h.attrs.has('aria-busy'), false);
});

test('forced page navigation flushes the draft without issuing AJAX', () => {
    const h = navigation();
    h.navigate('/destination', '#content', true);
    assert.deepEqual(h.events, ['flush']);
    assert.equal(h.window.location.href, '/destination');
    assert.equal(h.requests.length, 0);
});

test('a hash-only link leaves the current request and draft untouched', () => {
    const h = navigation();
    h.navigate('/first');
    h.navigate('#');
    assert.deepEqual(h.events, ['flush']);
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].aborted, false);
});

function ranking() {
    const requests = [], events = [], nodes = new Map();
    function node(selector) {
        if (!nodes.has(selector)) {
            nodes.set(selector, {
                length: 1, attrs: new Map(), content: '', checked: false, visible: false,
                show() { this.visible = true; return this; },
                hide() { this.visible = false; return this; },
                attr(key, value) { this.attrs.set(key, value); return this; },
                removeAttr(key) { this.attrs.delete(key); return this; },
                removeClass() { return this; }, addClass() { return this; },
                text(value) { this.content = value; return this; },
                html(value) { this.content = value; return this; },
                append(value) { this.appended = value; return this; },
                empty() { this.content = ''; return this; },
                is() { return this.checked; },
            });
        }
        return nodes.get(selector);
    }
    const $ = selector => node(selector);
    $.ajax = transport(requests, events);
    const window = {currentPage: 1, currentSearch: '', CONTEST_KEY: 'qa'};
    const document = {hidden: false}, location = {pathname: '/contest/qa/ranking/'};
    const history = {replaceState: (_state, _title, url) => events.push(url)};
    const context = vm.createContext({
        $, window, document, location, history, clearInterval: () => events.push('clearTimer'),
        localStorage: {getItem: () => null},
        snapshot_ranking: () => ({}), highlightFirstSolve() {}, apply_ranking_animations() {},
    });
    vm.runInContext(sourceFunction('templates/contest/media-js.html', 'buildRankingUrl') + '\n' +
        sourceFunction('templates/contest/media-js.html', 'update_ranking'), context);
    node('#ranking-container').content = 'old table';
    return {requests, events, window, document, location, node, update: context.update_ranking};
}

test('hidden tabs do not poll Ranking; an explicit user filter can still load', () => {
    const h = ranking();
    h.document.hidden = true;
    h.update();
    assert.equal(h.requests.length, 0);
    h.update({page: 1, search: 'student'});
    assert.equal(h.requests.length, 1);
});

test('automatic Ranking refresh does not overlap a pending request', () => {
    const h = ranking();
    h.update();
    h.update();
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].aborted, false);
});

test('a newer Ranking filter aborts the previous request and ignores all stale callbacks', () => {
    const h = ranking();
    h.update({page: 1, search: 'first'});
    h.update({page: 1, search: 'second'});
    const stale = h.requests[0].options;
    stale.success('stale table'); stale.error({}, 'timeout'); stale.complete();
    assert.equal(h.requests[0].aborted, true);
    assert.equal(h.node('#ranking-container').content, 'old table');
    assert.equal(h.node('#ranking-feedback').content, 'Đang cập nhật…');
    assert.equal(h.window._hnRankingRequest.xhr, h.requests[1]);
    assert.equal(h.events.filter(event => event.startsWith('?')).length, 0);
});

test('a Ranking network error keeps data and offers retry; completion clears busy UI', () => {
    const h = ranking();
    h.update({page: 1, search: 'student'});
    assert.equal(h.requests[0].options.timeout, 20000);
    h.requests[0].options.error({}, 'timeout');
    h.requests[0].options.complete();
    assert.equal(h.node('#ranking-container').content, 'old table');
    assert.match(h.node('#ranking-feedback').content, /Dữ liệu cũ vẫn được giữ/);
    assert.equal(h.node('#ranking-feedback').appended.content, 'Thử lại');
    assert.equal(h.node('#ranking-container').attrs.has('aria-busy'), false);
    assert.equal(h.node('#loading-gif').visible, false);
    assert.equal(h.window._hnRankingRequest.xhr, null);
});

test('a successful current Ranking request updates the table and matching filter URL', () => {
    const h = ranking();
    h.update({page: 2, search: 'nguyễn & an'});
    h.requests[0].options.success('new table');
    h.requests[0].options.complete();
    assert.equal(h.node('#ranking-container').content, 'new table');
    assert.equal(h.window.currentPage, 2);
    assert.equal(h.window.currentSearch, 'nguyễn & an');
    assert.ok(h.events.includes('?friend=0&virtual=0&page=2&search=nguy%E1%BB%85n%20%26%20an'));
    assert.equal(h.node('#ranking-feedback').content, '');
});

test('Ranking responses after changing route or disposing the controller cannot write data', () => {
    for (const disposal of ['route', 'controller']) {
        const h = ranking();
        h.update({page: 1});
        if (disposal === 'route') h.location.pathname = '/course/qa/grades';
        else h.window._hnRankingRequest = null;
        h.requests[0].options.success('wrong table');
        h.requests[0].options.error({}, 'error');
        assert.equal(h.node('#ranking-container').content, 'old table');
        assert.equal(h.events.filter(event => event.startsWith('?')).length, 0);
    }
});

test('Ranking polling stops when its page is no longer present', () => {
    const h = ranking();
    h.node('.contest-ranking-page #ranking-container').length = 0;
    h.update();
    assert.equal(h.requests.length, 0);
    assert.ok(h.events.includes('clearTimer'));
});
