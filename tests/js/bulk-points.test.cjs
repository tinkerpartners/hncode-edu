const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

test('bulk points follow the active contest row type after switching selectors', () => {
    function row(type, problem, quiz, {deleted = false, disabled = false} = {}) {
        const score = {value: '25', disabled, changes: 0, dispatchEvent() {this.changes++;}};
        return {
            dataset: type ? {contestItemType: type} : {}, score,
            querySelector(selector) {
                if (selector.includes('DELETE')) return deleted ? {} : null;
                if (selector.includes('-problem')) return {value: problem};
                if (selector.includes('-quiz')) return {value: quiz};
                return score;
            },
        };
    }
    const rows = [row('quiz', 'old-problem', ''), row('problem', 'new-problem', 'old-quiz'),
        row(null, 'lesson-problem', ''), row(null, '', ''), row(null, 'x', '', {deleted: true}),
        row(null, 'x', '', {disabled: true}), row(null, '', 'quiz')];
    let click;
    const status = {};
    const formset = {dataset: {prefix: 'rows'}, querySelectorAll: () => rows};
    const tool = {
        dataset: {prefix: 'rows', scoreField: 'points'},
        closest: () => ({querySelectorAll: () => [formset]}),
        querySelector(selector) {
            if (selector === 'input') return {value: '', reportValidity: () => true};
            if (selector === 'button') return {addEventListener: (_, handler) => {click = handler;}};
            return status;
        },
    };
    const document = {addEventListener: (_, handler) => handler(), querySelectorAll: () => [tool]};
    const source = fs.readFileSync('templates/widgets/bulk-problem-points-js.html', 'utf8');
    vm.runInNewContext(source.replace(/<\/?script>/g, ''), {document, Event: class {}});
    click();
    assert.deepEqual(rows.map(item => item.score.value), ['25', '100', '100', '25', '25', '25', '25']);
    assert.equal(rows[1].score.changes, 1);
    assert.match(status.textContent, /2/);
});
