import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// 决策变更（Phase 3 S1）：showSuggestions / closeFloatingProductSuggestions 已从销售页内联
// 副本统一到 order-entry-common.js（四页共用的单一实现）。
const source = fs.readFileSync(new URL('../../app/erp/static/order-entry-common.js',import.meta.url),'utf8');
const show = source.slice(source.indexOf('function showSuggestions('),source.indexOf('function bindAutocomplete('));
const scroll = source.slice(source.indexOf('function closeFloatingProductSuggestions('),source.indexOf('window.ErpOrderEntry = {'));

function fixture(kind, rect) {
  let shown=false, closed=0;
  const inside={};
  const box={id:kind==='customer'?'customer_suggestions':'product_suggestions_1',innerHTML:'',style:{},offsetWidth:280,offsetHeight:180,
    classList:{contains:name=>name=== (kind==='customer'?'suggest-list':'product-suggestions'),remove(){}},
    appendChild(){},setAttribute(){},matches:()=>shown,
    showPopover(){shown=true;},contains:target=>target===inside};
  const input={getBoundingClientRect:()=>rect||{left:50,top:200,bottom:240,width:280},setAttribute(){},removeAttribute(){},_closeSuggestions(){closed++;shown=false;}};
  const document={createElement:()=>({className:'',setAttribute(){},appendChild(){},addEventListener(){}}),
    querySelectorAll:selector=>selector.startsWith('.product-suggestions')&&kind==='customer'?[]:[box],
    querySelector:()=>input};
  const context=vm.createContext({document,window:{innerWidth:1440,innerHeight:900}});
  vm.runInContext(show+scroll,context);
  return {context,input,box,inside,get shown(){return shown;},get closed(){return closed;},open(){shown=true;}};
}

for (const kind of ['customer','product']) {
  test(`${kind} suggestions escape card stacking through the top layer`,()=>{
    const f=fixture(kind);
    f.context.showSuggestions(f.input,f.box,[{name:'fixture'}],()=>({}),()=>{});
    assert.equal(f.shown,true,'Suggestions remained underneath the sibling card');
    assert.equal(f.box.style.left,'50px');
    assert.equal(f.box.style.top,'244px');
  });
}

// 语义变更（2026-10-13）：滚动不再关闭候选，而是重算位置跟随输入框；
// 只有输入框真的滚出视口才关闭。旧断言「page scroll closes ...」钉的正是被推翻的行为。
test('page scroll keeps an open list anchored to its input instead of closing it',()=>{
  const f=fixture('customer');f.open();
  f.context.closeFloatingProductSuggestions({type:'scroll',target:{}});
  assert.equal(f.closed,0,'滚动时不应关闭候选，应跟随输入框重定位');
  assert.equal(f.shown,true);
  assert.equal(f.box.style.left,'50px');
  assert.equal(f.box.style.top,'244px');
});

test('list closes only once its input has scrolled out of the viewport',()=>{
  const f=fixture('customer',{left:50,top:-80,bottom:-40,width:280});f.open();
  f.context.closeFloatingProductSuggestions({type:'scroll',target:{}});
  assert.equal(f.closed,1);
  assert.equal(f.shown,false);
});

test('scrolling inside the customer menu preserves it; resizing closes it',()=>{
  const f=fixture('customer');f.open();
  f.context.closeFloatingProductSuggestions({type:'scroll',target:f.inside});
  assert.equal(f.closed,0);
  f.context.closeFloatingProductSuggestions({type:'resize',target:{}});
  assert.equal(f.closed,1);
});
