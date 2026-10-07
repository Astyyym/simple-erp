import assert from 'node:assert/strict';
import test from 'node:test';
import {clampPanAxis, PdfPanPreview} from '../../app/erp/static/js/pdf-pan-preview.mjs';

class Element extends EventTarget {
  constructor(width = 0, height = 0) {
    super();
    this.clientWidth = width;
    this.clientHeight = height;
    this.style = {};
    this.dataset = {};
    this.classes = new Set();
    this.classList = {add:name=>this.classes.add(name),remove:name=>this.classes.delete(name)};
    this.capture = null;
  }
  setPointerCapture(id) { this.capture = id; }
  hasPointerCapture(id) { return this.capture === id; }
  releasePointerCapture(id) { if (this.capture === id) this.capture = null; }
  focus() {}
}

globalThis.ResizeObserver = class {observe() {} disconnect() {}};
function pointer(element, type, x, y, extras = {}) {
  const event = new Event(type, {cancelable:true});
  Object.assign(event, {button:0,pointerId:1,clientX:x,clientY:y,...extras});
  element.dispatchEvent(event);
}
function viewer() {
  const viewport = new Element(420,500);
  const board = new Element();
  board.closest = selector => selector === '.pdf-page' ? board : null;
  const preview = new PdfPanPreview({viewport,board});
  preview.baseWidth = 794;
  preview.baseHeight = 1123;
  preview.resize(true);
  return {preview,viewport,board};
}

test('small paper is centered; oversized paper cannot be dragged out of bounds', () => {
  assert.equal(clampPanAxis(90,420,200),110);
  assert.equal(clampPanAxis(900,420,800),12);
  assert.equal(clampPanAxis(-900,420,800),-392);
  assert.equal(clampPanAxis(-40,420,800),-40);
});

test('zoom clamps, keeps the viewport anchor, and reset returns to top', () => {
  const {preview} = viewer();
  const initialScale = preview.scale;
  preview.setZoom(2);
  assert.equal(preview.scale,initialScale * 2);
  assert(preview.x < 12 && preview.y < 12);
  preview.panBy(-99999,-99999);
  preview.setZoom(1,true);
  assert.equal(preview.zoom,1);
  assert.equal(preview.y,12);
  assert.equal(preview.x,12);
  preview.setZoom(99);
  assert.equal(preview.zoom,2);
  preview.setZoom(.01);
  assert.equal(preview.zoom,.6);
});

test('paper captures the pointer, pans both axes, and releases outside on cancellation', () => {
  const {preview,board,viewport} = viewer();
  preview.setZoom(2);
  const before = {x:preview.x,y:preview.y};
  pointer(board,'pointerdown',120,180);
  assert.equal(viewport.capture,1);
  assert(viewport.classes.has('is-dragging'));
  pointer(viewport,'pointermove',80,130);
  assert.equal(preview.x,before.x-40);
  assert.equal(preview.y,before.y-50);
  pointer(viewport,'pointercancel',80,130);
  assert.equal(viewport.capture,null);
  assert(!viewport.classes.has('is-dragging'));
  pointer(viewport,'pointermove',50,80);
  assert.equal(preview.x,before.x-40);
});

test('right click cannot start dragging; resizing preserves a reachable document', () => {
  const {preview,board,viewport} = viewer();
  pointer(board,'pointerdown',100,100,{button:2});
  assert.equal(viewport.capture,null);
  preview.setZoom(2);
  preview.panBy(-99999,-99999);
  viewport.clientWidth=320;
  viewport.clientHeight=700;
  preview.resize();
  assert.equal(preview.x,clampPanAxis(preview.x,320,preview.baseWidth*preview.scale));
  assert.equal(preview.y,clampPanAxis(preview.y,700,preview.baseHeight*preview.scale));
});
