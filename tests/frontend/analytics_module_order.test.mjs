import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const html=readFileSync(new URL('../../designs/simple-erp-inventory-analytics/counterparty-layout-prototype-v4.html',import.meta.url),'utf8');

test('the whole three-tab analysis card precedes the whole product card after reconciliation entry',()=>{
  const auxiliary=html.match(/<section class="surface panel secondary"[\s\S]*?<\/section>/)?.[0];
  const product=html.match(/<section class="surface panel" aria-labelledby="productTableTitle"[\s\S]*?<\/section>/)?.[0];
  assert(auxiliary&&product,'Both full cards must exist');
  assert(html.indexOf('id="openRecon"')<html.indexOf(auxiliary),'Reconciliation entry stays above both cards');
  assert(html.indexOf(auxiliary)<html.indexOf(product),'Move the entire auxiliary card before the product card');
  for(const id of ['heatTab','rankTab','healthTab','heatPanel','rankPanel','healthPanel','heatMetric','rankingMode'])assert(auxiliary.includes(`id="${id}"`),`${id} stays inside the upper card`);
  for(const id of ['productTableTitle','analysisScope','analysisSort','analysisRows','analysisTableNote'])assert(product.includes(`id="${id}"`),`${id} stays inside the lower card`);
});
