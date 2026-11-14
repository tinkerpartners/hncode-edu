const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const {sourceFunction} = require('./helpers/source.cjs');

function classList() {
    const values = new Set();
    return {
        toggle(name, enabled) { if (enabled) values.add(name); else values.delete(name); },
        remove(name) { values.delete(name); },
        contains(name) { return values.has(name); },
    };
}

function harness(mobile = true) {
    const parentElement = {classList: classList()};
    const attrs = new Map([['aria-expanded', 'false']]);
    const submenu = {
        parentElement,
        getAttribute: name => attrs.get(name),
        setAttribute: (name, value) => attrs.set(name, value),
    };
    let focusCount = 0;
    const toggle = {
        classList: classList(),
        setAttribute(name, value) { this[name] = value; },
        focus() { focusCount++; },
    };
    const list = {style: {}, querySelectorAll: () => [submenu]};
    const context = vm.createContext({
        window: {matchMedia: () => ({matches: mobile})},
        document: {
            querySelectorAll: () => [submenu],
            getElementById: id => id === 'navicon' ? toggle : list,
        },
    });
    vm.runInContext(sourceFunction('resources/common.js', 'registerMobileSubmenus') + '\n' +
        sourceFunction('resources/common.js', 'closeMobileNavigation'), context);
    return {context, submenu, parentElement, toggle, list, focusCount: () => focusCount};
}

function tap(h) {
    let prevented = false, stopped = false;
    h.submenu.onclick({preventDefault() { prevented = true; }, stopPropagation() { stopped = true; }});
    assert.ok(prevented && stopped);
}

test('phone submenu toggles without following the parent link', () => {
    const h = harness();
    h.context.registerMobileSubmenus();
    tap(h);
    assert.equal(h.submenu.getAttribute('aria-expanded'), 'true');
    assert.ok(h.parentElement.classList.contains('is-submenu-open'));
    tap(h);
    assert.equal(h.submenu.getAttribute('aria-expanded'), 'false');
    assert.equal(h.parentElement.classList.contains('is-submenu-open'), false);
});

test('rebinding after AJAX navigation cannot turn one tap into two toggles', () => {
    const h = harness();
    h.context.registerMobileSubmenus();
    h.context.registerMobileSubmenus();
    tap(h);
    assert.equal(h.submenu.getAttribute('aria-expanded'), 'true');
});

test('closing the drawer resets submenu state and optionally restores focus', () => {
    const h = harness();
    h.context.registerMobileSubmenus();
    tap(h);
    h.context.closeMobileNavigation(true);
    assert.equal(h.list.style.display, 'none');
    assert.equal(h.toggle['aria-expanded'], 'false');
    assert.equal(h.submenu.getAttribute('aria-expanded'), 'false');
    assert.equal(h.parentElement.classList.contains('is-submenu-open'), false);
    assert.equal(h.focusCount(), 1);
    h.context.closeMobileNavigation(false);
    assert.equal(h.focusCount(), 1);
});

test('desktop hover navigation is unaffected by the phone submenu handler', () => {
    const h = harness(false);
    h.context.registerMobileSubmenus();
    tap(h);
    assert.equal(h.submenu.getAttribute('aria-expanded'), 'false');
});
