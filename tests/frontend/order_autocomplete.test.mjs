import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Test the page's actual autocomplete controller separately from browser layout.
// 决策变更（Phase 3 S1）：联想控制器已统一到 order-entry-common.js（销售页原先内联一份副本）。
const source=fs.readFileSync(new URL('../../app/erp/static/order-entry-common.js',import.meta.url),'utf8');
const start=source.indexOf('function bindAutocomplete(');
const end=source.indexOf('window.ErpOrderEntry = {',start);
assert(start>=0 && end>start);
class Input extends EventTarget {
  constructor() {super();this.value='fixture';this.attributes=new Map();}
  setAttribute(key,value) {this.attributes.set(key,value);}
  removeAttribute(key) {this.attributes.delete(key);}
  getAttribute(key) {return this.attributes.get(key);}
}
function controller(loadItems=async()=>[{name:'fixture'}], emptyText) {
  const input=new Input();
  const classes=new Set(['d-none']);
  const box={id:'product_suggestions_1',classList:{contains:name=>classes.has(name),add:name=>classes.add(name),remove:name=>classes.delete(name)}};
  const callbacks=[];
  const shown=[];
  const document={activeElement:input};
  const context=vm.createContext({document,window:{setTimeout:callback=>callbacks.push(callback)},
    showSuggestions(input,box,items,render,onPick,text) {shown.push({count:items.length,text});box.classList.remove('d-none');input.setAttribute('aria-expanded','true');},
    closeSuggestions(input,box) {box.classList.add('d-none');input.setAttribute('aria-expanded','false');},
  });
  vm.runInContext(source.slice(start,end),context);
  context.bindAutocomplete(input,box,loadItems,()=>{},()=>{},()=>{},emptyText);
  return {input,document,callbacks,box,shown};
}
const settle=async()=>{await Promise.resolve();await Promise.resolve();};

test('a delayed blur does not close a list after the same input has been refocused', async () => {
  const {input,callbacks,box}=controller();
  input.dispatchEvent(new Event('input'));await settle();
  input.dispatchEvent(new Event('blur'));
  input.dispatchEvent(new Event('focus'));await settle();
  callbacks.forEach(callback=>callback());
  assert(!box.classList.contains('d-none'),'An obsolete blur callback closed the refocused list');
});

test('closing suggestions invalidates an outstanding server response', async () => {
  let resolve;
  const pending=new Promise(done=>{resolve=done;});
  const {input,box}=controller(()=>pending);
  input.dispatchEvent(new Event('input'));input._closeSuggestions();
  resolve([{name:'late fixture'}]);await settle();
  assert(box.classList.contains('d-none'));
  assert.equal(input.getAttribute('aria-expanded'),'false');
});

// Phase 3 S2：中文输入法组合期间的 value 是拼音串，查询与渲染都会误导用户
//（用户会在组合阶段看到假的「无匹配项」）。组合期间不查询、不渲染，上屏后再查一次。
test('IME composition does not query or render the misleading empty state', async () => {
  const queries=[];
  const {input,shown}=controller(async query=>{queries.push(query);return [];});
  input.dispatchEvent(new Event('compositionstart'));
  input.value='mie';
  const composing=new Event('input');
  Object.defineProperty(composing,'isComposing',{value:true});
  input.dispatchEvent(composing);await settle();
  assert.deepEqual(queries,[],'组合期间不应发起查询');
  assert.deepEqual(shown,[],'组合期间不应渲染候选/空状态');
  input.value='灭火器';
  input.dispatchEvent(new Event('compositionend'));await settle();
  assert.deepEqual(queries,['灭火器'],'上屏后应按最终值查询一次');
  assert.equal(shown.length,1);
});

// Phase 3 S2：零建档路径的输入框不能把「没有已建档商品」说成「无匹配项」。
test('empty-result copy comes from the caller for zero-setup inputs', async () => {
  const {input,shown}=controller(async()=>[], '无匹配项 · 保存时会新建该商品');
  input.dispatchEvent(new Event('input'));await settle();
  assert.equal(shown.length,1);
  assert.equal(shown[0].count,0);
  assert.equal(shown[0].text,'无匹配项 · 保存时会新建该商品');
});
