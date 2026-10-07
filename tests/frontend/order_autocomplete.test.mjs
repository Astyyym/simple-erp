import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Test the page's actual autocomplete controller separately from browser layout.
const source=fs.readFileSync(new URL('../../app/erp/templates/orders/new.html',import.meta.url),'utf8');
const start=source.indexOf('function bindAutocomplete(');
const end=source.indexOf('const customerInput =',start);
assert(start>=0 && end>start);
class Input extends EventTarget {
  constructor() {super();this.value='fixture';this.attributes=new Map();}
  setAttribute(key,value) {this.attributes.set(key,value);}
  removeAttribute(key) {this.attributes.delete(key);}
  getAttribute(key) {return this.attributes.get(key);}
}
function controller(loadItems=async()=>[{name:'fixture'}]) {
  const input=new Input();
  const classes=new Set(['d-none']);
  const box={id:'product_suggestions_1',classList:{contains:name=>classes.has(name),add:name=>classes.add(name),remove:name=>classes.delete(name)}};
  const callbacks=[];
  const document={activeElement:input};
  const context=vm.createContext({document,window:{setTimeout:callback=>callbacks.push(callback)},
    showSuggestions(input,box) {box.classList.remove('d-none');input.setAttribute('aria-expanded','true');},
    closeSuggestions(input,box) {box.classList.add('d-none');input.setAttribute('aria-expanded','false');},
  });
  vm.runInContext(source.slice(start,end),context);
  context.bindAutocomplete(input,box,loadItems,()=>{},()=>{});
  return {input,document,callbacks,box};
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
