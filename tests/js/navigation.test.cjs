const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const {sourceFunction} = require('./helpers/source.cjs');

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
