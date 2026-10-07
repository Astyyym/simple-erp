import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const template = fs.readFileSync(new URL('../../app/erp/templates/orders/new.html', import.meta.url), 'utf8');

test('order entry uses the shared desktop preview instead of an embedded side panel', () => {
  assert.equal(template.includes('id="previewPanel"'), false);
  assert.equal(template.includes('id="previewPages"'), false);
  assert.equal(template.includes('async function openOrderPreview('), false);
  assert.equal(template.includes("openPrintUrl(openPrintLink.href)"), true);
  assert.equal(template.includes('保存成功后进入统一打印预览'), true);
});