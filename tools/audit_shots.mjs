#!/usr/bin/env node
/**
 * 产品审核用全页面截图：给「页面 + 业务逻辑」做一份走查素材。
 *
 * 与 tools/shots.mjs 的区别：
 *  - 覆盖全部桌面主路由 + 关键子页/详情页（不是只有 8 页 README 图）
 *  - 整页长截图（captureBeyondViewport），能看完整页内容
 *  - 数据根同样强制隔离到 .alma/shots/demo_data，绝不碰正式账本
 *
 * 用法：node tools/audit_shots.mjs
 * 输出：.alma/audit/shots/*.jpg
 */

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const IS_WIN = process.platform === 'win32';
const VENV_PY = path.join(ROOT, '.venv', IS_WIN ? 'Scripts/python.exe' : 'bin/python');
const DEMO_ROOT = path.join(ROOT, '.alma', 'shots', 'demo_data');
const OUT_DIR = path.join(ROOT, '.alma', 'audit', 'shots');
const PROFILE_ROOT = path.join(ROOT, '.alma', 'audit', 'chrome');

const APP_PORT = 5299;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  const hit = candidates.find((p) => fs.existsSync(p));
  if (!hit && !process.env.CHROME_PATH) {
    console.error('找不到 Chrome/Edge。用 CHROME_PATH 环境变量指定。');
    process.exit(1);
  }
  return process.env.CHROME_PATH || hit;
}
const CHROME = findChrome();

async function waitFor(label, probe, { timeout = 40000, interval = 400 } = {}) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    try { if (await probe()) return true; } catch (e) {
      last = e;
      if (String(e.message).includes('拒绝截图')) throw e;
    }
    await sleep(interval);
  }
  throw new Error(`等待超时：${label}${last ? `（${last.message}）` : ''}`);
}

async function withCdp(fn, { port, profile, mobile = false }) {
  const args = [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--hide-scrollbars',
    '--disable-gpu', '--disable-extensions', 'about:blank',
  ];
  const proc = spawn(CHROME, args, { stdio: 'ignore' });
  try {
    await waitFor('Chrome 调试端口', async () => {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      return r.ok;
    }, { timeout: 25000, interval: 250 });
    const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const target = list.find((t) => t.type === 'page');
    if (!target) throw new Error('Chrome 没有可用页面目标');
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true });
      ws.addEventListener('error', rej, { once: true });
    });
    let id = 0;
    const pending = new Map();
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      const p = pending.get(m.id);
      if (!p) return;
      pending.delete(m.id);
      if (m.error) p.reject(new Error(JSON.stringify(m.error)));
      else p.resolve(m.result);
    });
    const send = (method, params = {}) => new Promise((res, rej) => {
      const mid = ++id;
      pending.set(mid, { resolve: res, reject: rej });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
    await send('Page.enable');
    await send('Runtime.enable');
    if (mobile) {
      await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    } else {
      await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1400, deviceScaleFactor: 1, mobile: false });
    }
    return await fn({ send });
  } finally {
    try { proc.kill(); } catch { /* 已退出 */ }
  }
}

/** 量出解链后正文卡片真实底部高度（避免底部大片空白）。 */
async function measureFullPage(send) {
  const { result } = await send('Runtime.evaluate', {
    expression: `(() => {
      const content = document.querySelector('.content');
      const pageBody = document.querySelector('.page-body');
      let h = 0;
      if (content) h = Math.max(h, content.getBoundingClientRect().bottom + window.scrollY);
      if (pageBody) h = Math.max(h, pageBody.getBoundingClientRect().bottom + window.scrollY);
      // 加上 .content 的下外边距（10px 左右）
      h += 24;
      return JSON.stringify({ h: Math.ceil(Math.max(h, 900)) });
    })()`,
    returnByValue: true,
  });
  return JSON.parse(result.value);
}

/**
 * 本系统布局是 .app-shell(height:100vh;overflow:hidden) → .content → .page-body(overflow:auto)，
 * 只有 .page-body 内部滚动，document 本身永远 900 高。
 * 整页截图前先把外壳「解链」成自然高度，让所有内容摊开，再截整个文档。
 * 返回一个还原函数。
 */
async function unchainShell(send, sidebarMin) {
  await send('Runtime.evaluate', {
    expression: `(() => {
      const css = (sel, kv) => { const e = document.querySelector(sel); if (e) Object.assign(e.style, kv); };
      css('html', { height: 'auto', overflow: 'auto' });
      css('body', { height: 'auto', overflow: 'auto' });
      css('.app-shell', { height: 'auto', overflow: 'visible', alignItems: 'flex-start' });
      css('.content', { height: 'auto', overflow: 'visible' });
      css('.page-body', { overflow: 'visible' });
      const sb = document.querySelector('.sidebar');
      if (sb && ${sidebarMin}) sb.style.minHeight = '${sidebarMin}px';
      // 让侧栏随内容拉满高度，避免左侧白条
      window.__orig = true;
    })()`,
  });
  await sleep(400);
}

async function captureFull(send, file, quality = 84) {
  await unchainShell(send, 0);
  const size = await measureFullPage(send);
  const width = 1440;
  const height = size.h;
  // 侧栏拉到同高，避免左侧白条
  await send('Runtime.evaluate', {
    expression: `(() => { const sb = document.querySelector('.sidebar'); if (sb) sb.style.minHeight = '${height - 20}px'; })()`,
  });
  await sleep(250);
  const { data } = await send('Page.captureScreenshot', {
    format: 'jpeg',
    quality,
    captureBeyondViewport: true,
    clip: { x: 0, y: 0, width, height, scale: 1 },
  });
  fs.writeFileSync(file, Buffer.from(data, 'base64'));
  return { kb: Math.round(fs.statSync(file).size / 1024), width, height };
}

function startPython(args, extraEnv, logName) {
  const buf = [];
  const proc = spawn(VENV_PY, args, {
    cwd: ROOT,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', ...extraEnv },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  proc.stdout.on('data', (d) => buf.push(d.toString()));
  proc.stderr.on('data', (d) => buf.push(d.toString()));
  proc.on('exit', (code) => {
    if (code !== 0 && logName) { try { fs.writeFileSync(path.join(OUT_DIR, logName), buf.join('')); } catch { /* */ } }
  });
  return proc;
}

async function waitHealth(port) {
  await waitFor(`服务 ${port}`, async () => {
    const r = await fetch(`http://127.0.0.1:${port}/health`);
    if (!r.ok) return false;
    const j = await r.json();
    const root = String(j.data_root || '').replace(/\\/g, '/');
    if (!root.includes('/.alma/shots/demo_data')) {
      throw new Error(`拒绝截图：数据根不是隔离目录（${j.data_root || '未知'}）。`);
    }
    return true;
  }, { timeout: 90000 });
}

function downscale(files, targetWidth) {
  if (!files.length) return;
  const code = [
    'import json',
    'from PIL import Image',
    `files = json.loads(${JSON.stringify(JSON.stringify(files))})`,
    'for f in files:',
    '    im = Image.open(f)',
    `    if im.width > ${targetWidth}:`,
    `        h = round(im.height * ${targetWidth} / im.width)`,
    `        im = im.resize((${targetWidth}, h), Image.LANCZOS)`,
    '    im.save(f, "JPEG", quality=84, optimize=True)',
    '    print(json.dumps({"f": f, "w": im.width, "h": im.height}))',
  ].join('\n');
  const r = spawnSync(VENV_PY, ['-c', code], { cwd: ROOT, encoding: 'utf-8' });
  if (r.status !== 0) console.warn(`  缩放跳过：${String(r.stderr || '').trim().split('\n').pop()}`);
}

/**
 * 页面清单。id 用于文件名；group 分组；title 中文标题；desc 审核关注点；
 * url 路由；waitExtra 额外等待毫秒（表单/图表多的页）。
 */
const PAGES = [
  // 一、工作台
  { id: '01-workbench', group: '一、工作台', title: '工作台（首页）', url: '/', desc: '进入系统后的总览：今日概览、快捷入口、待办与提醒。' },
  // 二、开单（四类）
  { id: '02-sales-new', group: '二、开单', title: '销售单 · 新建', url: '/orders/new', waitExtra: 1500, desc: '核心开单页：选客户（输入即模糊匹配，须点候选）→ 逐行选商品、数量、单价。单价取客户专属价，无专属价留空手输。默认 1 行。' },
  { id: '03-return-new', group: '二、开单', title: '退货单 · 新建', url: '/orders/return/new', waitExtra: 1500, desc: '退货开单：从历史销售单选来源，回填可退行；单价逻辑与销售一致。' },
  { id: '04-purchase-new', group: '二、开单', title: '拿货单 · 新建', url: '/purchases/new', waitExtra: 1500, desc: '拿货（进货）开单：NH 单号，按商品记录拿货数量。' },
  { id: '05-purchase-return-new', group: '二、开单', title: '退拿货单 · 新建', url: '/purchases/return/new', waitExtra: 1500, desc: '退拿货：从历史拿货单选来源，回填可退行。' },
  // 三、单据管理
  { id: '06-documents-list', group: '三、单据管理', title: '单据列表', url: '/orders/', waitExtra: 1500, desc: '销售/退货/拿货/退拿货统一列表：筛选（日期、客户、类型、状态）、分页、批量操作、导出 Excel。' },
  { id: '07-sale-detail', group: '三、单据管理', title: '销售单 · 详情', url: '/orders/5', waitExtra: 1200, desc: '单据详情：明细行、金额汇总、状态、操作（编辑/打印/作废）。' },
  { id: '08-sale-edit', group: '三、单据管理', title: '销售单 · 编辑', url: '/orders/5/edit', waitExtra: 1500, desc: '重编辑：保号保日期，改明细后重新保存。' },
  { id: '09-return-detail', group: '三、单据管理', title: '退货单 · 详情', url: '/orders/91', waitExtra: 1200, desc: '退货单详情与来源关联。' },
  { id: '10-purchase-detail', group: '三、单据管理', title: '拿货单 · 详情', url: '/purchases/5', waitExtra: 1200, desc: '拿货单详情。' },
  { id: '11-purchase-return-detail', group: '三、单据管理', title: '退拿货单 · 详情', url: '/purchases/return/1', waitExtra: 1200, desc: '退拿货单详情与来源关联。' },
  // 四、账款管理
  { id: '12-accounts', group: '四、账款管理', title: '账款管理', url: '/accounts/', waitExtra: 1200, desc: '客户账款总览：余额、收款、调整、作废入口。客户字段为输入即模糊匹配。' },
  { id: '13-reconciliation', group: '四、账款管理', title: '往来对账', url: '/accounts/reconciliation?snapshot_id=6', waitExtra: 1500, desc: '两段式对账单：勾选纳入的订单/拿货，生成对账 PDF（A4 竖版）。' },
  // 五、数据分析
  { id: '14-analytics', group: '五、数据分析', title: '数据分析', url: '/analytics/', waitExtra: 1800, desc: '销售/商品/客户多维分析、排行与月度趋势图。' },
  // 六、基础资料
  { id: '15-products-list', group: '六、基础资料', title: '商品管理 · 列表', url: '/products/', waitExtra: 1200, desc: '商品列表：搜索、质量筛选、排序、批量删除、导入/导出。' },
  { id: '16-product-edit', group: '六、基础资料', title: '商品 · 编辑', url: '/products/1/edit', waitExtra: 1200, desc: '商品编辑：名称、规格、单位、图片上传、客户专属价相关。' },
  { id: '17-customers-list', group: '六、基础资料', title: '客户管理 · 列表', url: '/customers/', waitExtra: 1200, desc: '客户列表：搜索、质量筛选、批量删除、导入/导出、开账余额。' },
  { id: '18-customer-edit', group: '六、基础资料', title: '客户 · 编辑', url: '/customers/1/edit', waitExtra: 1200, desc: '客户编辑：名称、联系方式、开账余额、专属价关联。' },
  { id: '19-docs-list', group: '六、基础资料', title: '资料文档', url: '/docs/', waitExtra: 1200, desc: '资料文档库：分类、上传、预览、打印、编辑。' },
  // 七、回收站
  { id: '20-recycle', group: '七、回收站', title: '回收站', url: '/recycle/', waitExtra: 1200, desc: '已删单据/资料的可恢复区：恢复、彻底删除、清空。' },
  // 八、系统设置
  { id: '21-settings', group: '八、系统设置', title: '系统设置', url: '/settings/', waitExtra: 1800, desc: '设置总页：打印纸型/偏移、显示缩放、备份还原、手机端配对、数据迁移、清空数据等分区。' },
];

async function main() {
  if (!fs.existsSync(VENV_PY)) { console.error(`找不到 ${VENV_PY}`); process.exit(1); }
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.mkdirSync(PROFILE_ROOT, { recursive: true });

  const env = { ERP_PORT: String(APP_PORT), ERP_DATA_ROOT: DEMO_ROOT };
  let app = null;
  const manifest = [];
  try {
    console.log(`启动隔离实例（端口 ${APP_PORT}，数据根 .alma/shots/demo_data）…`);
    app = startPython([path.join('app', 'app.py')], env, 'app.log');
    await waitHealth(APP_PORT);

    for (let i = 0; i < PAGES.length; i += 1) {
      const p = PAGES[i];
      process.stdout.write(`  ${p.id} … `);
      const file = path.join(OUT_DIR, `${p.id}.jpg`);
      let info;
      try {
        info = await withCdp(async ({ send }) => {
          await send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}${p.url}` });
          await sleep(3200 + (p.waitExtra || 0));
          return await captureFull(send, file);
        }, { port: 9500 + i, profile: path.join(PROFILE_ROOT, `p-${i}`) });
        manifest.push({ ...p, file: path.basename(file), width: info.width, height: info.height, kb: info.kb });
        console.log(`ok (${info.width}x${info.height}, ${info.kb}KB)`);
      } catch (e) {
        console.log(`失败：${e.message}`);
        manifest.push({ ...p, file: null, error: e.message });
      }
    }
  } finally {
    if (app) { try { app.kill(); } catch { /* */ } }
  }

  const files = manifest.filter((m) => m.file).map((m) => path.join(OUT_DIR, m.file));
  console.log('\n缩放中…');
  downscale(files, 1440);

  // 重新读回缩放后的尺寸
  fs.writeFileSync(path.join(OUT_DIR, 'manifest.json'), JSON.stringify(manifest, null, 2));
  console.log(`\n完成 → ${OUT_DIR}`);
  for (const m of manifest) console.log(`  ${(m.file || '—').padEnd(24)} ${m.error ? 'ERROR ' + m.error : `${m.kb}KB`}`);
}

main().then(() => process.exit(0), (e) => { console.error(`\n失败：${e.message}`); process.exit(1); });
