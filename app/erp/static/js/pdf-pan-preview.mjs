// Display the generated PDF pages; never reflow the receipt or modify its bytes.
const PADDING = 12;
const PAGE_GAP = 16;
const PDF_CSS_SCALE = 96 / 72;
const vendor = new URL('../vendor/pdfjs/', import.meta.url);
let libraryPromise;

async function pdfLibrary() {
  if (!libraryPromise) {
    libraryPromise = import(new URL('build/pdf.min.mjs', vendor).href).then(library => {
      library.GlobalWorkerOptions.workerSrc = new URL('build/pdf.worker.min.mjs', vendor).href;
      return library;
    }).catch(error => {
      libraryPromise = null;
      throw error;
    });
  }
  return libraryPromise;
}

export function clampPanAxis(value, viewportSize, contentSize) {
  if (contentSize <= viewportSize - PADDING * 2) return (viewportSize - contentSize) / 2;
  return Math.max(viewportSize - PADDING - contentSize, Math.min(PADDING, value));
}

export class PdfPanPreview {
  constructor({viewport, board, onZoomChange = () => {}}) {
    this.viewport = viewport;
    this.board = board;
    this.onZoomChange = onZoomChange;
    this.baseWidth = 0;
    this.baseHeight = 0;
    this.zoom = 1;
    this.scale = 1;
    this.x = 0;
    this.y = PADDING;
    this.viewWidth = 0;
    this.viewHeight = 0;
    this.drag = null;
    this.document = null;
    this.loadingTask = null;
    this.generation = 0;
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(viewport);

    board.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !this.baseWidth || this.drag || !event.target.closest('.pdf-page')) return;
      event.preventDefault();
      viewport.focus({preventScroll:true});
      viewport.setPointerCapture(event.pointerId);
      this.drag = {id:event.pointerId, clientX:event.clientX, clientY:event.clientY, x:this.x, y:this.y};
      viewport.classList.add('is-dragging');
    });
    viewport.addEventListener('pointermove', event => {
      if (!this.drag || event.pointerId !== this.drag.id) return;
      this.x = this.drag.x + event.clientX - this.drag.clientX;
      this.y = this.drag.y + event.clientY - this.drag.clientY;
      this.applyTransform();
    });
    const stopDrag = event => {
      if (!this.drag || event.pointerId !== this.drag.id) return;
      this.stopDragging();
    };
    viewport.addEventListener('pointerup', stopDrag);
    viewport.addEventListener('pointercancel', stopDrag);
    viewport.addEventListener('lostpointercapture', stopDrag);
    viewport.addEventListener('wheel', event => {
      if (!this.baseWidth) return;
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1;
      event.preventDefault();
      this.panBy(-event.deltaX * unit, -event.deltaY * unit);
    }, {passive:false});
    viewport.addEventListener('keydown', event => {
      const offsets = {ArrowLeft:[48,0], ArrowRight:[-48,0], ArrowUp:[0,48], ArrowDown:[0,-48]};
      if (offsets[event.key]) {
        event.preventDefault();
        this.panBy(...offsets[event.key]);
      } else if (event.key === 'Home') {
        event.preventDefault();
        this.setZoom(1, true);
      }
    });
  }

  stopDragging() {
    const id = this.drag?.id;
    this.drag = null;
    this.viewport.classList.remove('is-dragging');
    if (id !== undefined && this.viewport.hasPointerCapture(id)) this.viewport.releasePointerCapture(id);
  }

  applyTransform() {
    const width = this.viewport.clientWidth;
    const height = this.viewport.clientHeight;
    this.x = clampPanAxis(this.x, width, this.baseWidth * this.scale);
    this.y = clampPanAxis(this.y, height, this.baseHeight * this.scale);
    this.board.style.transform = `translate(${this.x}px, ${this.y}px) scale(${this.scale})`;
  }

  resize(reset = false) {
    const width = this.viewport.clientWidth;
    const height = this.viewport.clientHeight;
    // Closed/animating panels may briefly have zero width. Keep the last view.
    if (!this.baseWidth || width <= PADDING * 2 || !height) return;
    const anchorX = ((this.viewWidth || width) / 2 - this.x) / this.scale;
    const anchorY = ((this.viewHeight || height) / 2 - this.y) / this.scale;
    const fit = Math.min(1, (width - PADDING * 2) / this.baseWidth);
    this.scale = fit * this.zoom;
    this.x = reset ? (width - this.baseWidth * this.scale) / 2 : width / 2 - anchorX * this.scale;
    this.y = reset ? PADDING : height / 2 - anchorY * this.scale;
    this.viewWidth = width;
    this.viewHeight = height;
    this.applyTransform();
  }

  setZoom(value, reset = false) {
    this.stopDragging();
    this.zoom = Math.round(Math.max(.6, Math.min(2, value)) * 10) / 10;
    this.resize(reset);
    this.onZoomChange(this.zoom);
  }

  panBy(dx, dy) {
    this.x += dx;
    this.y += dy;
    this.applyTransform();
  }

  async clear() {
    this.generation += 1;
    this.stopDragging();
    this.baseWidth = 0;
    this.baseHeight = 0;
    // Release canvas backing stores before dropping the DOM nodes.
    for (const canvas of this.board.querySelectorAll('canvas')) canvas.width = canvas.height = 0;
    this.board.replaceChildren();
    this.board.style.width = '0px';
    this.board.style.height = '0px';
    this.board.dataset.pages = '0';
    const task = this.loadingTask;
    this.loadingTask = null;
    this.document = null;
    if (task) await task.destroy();
  }

  async load(blob) {
    await this.clear();
    const generation = this.generation;
    const library = await pdfLibrary();
    if (generation !== this.generation) return;
    const bytes = new Uint8Array(await blob.arrayBuffer());
    this.loadingTask = library.getDocument({
      data:bytes,
      cMapUrl:new URL('cmaps/', vendor).href,
      cMapPacked:true,
      standardFontDataUrl:new URL('standard_fonts/', vendor).href,
      wasmUrl:new URL('wasm/', vendor).href,
    });
    try {
      const pdf = await this.loadingTask.promise;
      if (generation !== this.generation) return;
      this.document = pdf;
      const fragment = document.createDocumentFragment();
      let width = 0;
      let height = 0;
      // Render every original page, not just the first page of a long order.
      for (let number = 1; number <= pdf.numPages; number++) {
        const page = await pdf.getPage(number);
        if (generation !== this.generation) return;
        const view = page.getViewport({scale:PDF_CSS_SCALE});
        const pixels = Math.min(2, Math.max(1.5, window.devicePixelRatio || 1));
        const canvas = document.createElement('canvas');
        canvas.className = 'pdf-page';
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', `单据 PDF 第 ${number} 页，共 ${pdf.numPages} 页`);
        canvas.style.width = `${view.width}px`;
        canvas.style.height = `${view.height}px`;
        canvas.width = Math.ceil(view.width * pixels);
        canvas.height = Math.ceil(view.height * pixels);
        await page.render({canvas, viewport:view, transform:[pixels,0,0,pixels,0,0]}).promise;
        if (generation !== this.generation) return;
        fragment.appendChild(canvas);
        width = Math.max(width, view.width);
        height += view.height;
      }
      this.baseWidth = width;
      this.baseHeight = height + PAGE_GAP * (pdf.numPages - 1);
      this.board.replaceChildren(fragment);
      this.board.style.width = `${this.baseWidth}px`;
      this.board.style.height = `${this.baseHeight}px`;
      this.board.dataset.pages = String(pdf.numPages);
      this.setZoom(1, true);
      return pdf.numPages;
    } catch (error) {
      if (generation === this.generation) await this.clear();
      throw error;
    }
  }
}
