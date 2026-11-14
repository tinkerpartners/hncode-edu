const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../../..');
function sourceFunction(file, name) {
    const source = readFileSync(path.join(root, file), 'utf8');
    const declaration = source.match(new RegExp(`^([ \t]*)function ${name}\\(`, 'm'));
    assert.ok(declaration, `Missing source function ${name}`);
    const indent = declaration[1];
    const oneLine = source.match(new RegExp(`^${indent}function ${name}\\([^\\n]*\\}\\r?$`, 'm'));
    const multiple = source.match(new RegExp(`^${indent}function ${name}\\([^]*?^${indent}}\\r?$`, 'm'));
    assert.ok(oneLine || multiple, `Missing source body ${name}`);
    return (oneLine || multiple)[0];
}
module.exports = {sourceFunction};
