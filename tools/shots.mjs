#!/usr/bin/env node
/**
 * 重拍 README 界面截图（docs/images/shot-*.jpg）。
 *
 * 为什么需要这个脚本：UI 改版后截图会过期，手动截十张要二十分钟、没人记得做。
 * 这里把「生成演示数据 → 起隔离实例 → 逐页 CDP 截图 → 落盘压缩 JPG」全自动化，
 * 一条命令重拍全套，且**绝不读取也不写入门店的真实账本**。
 *
 * 用法：
 *   node tools/shots.mjs                   重拍全部（首次会自动生成演示数据）
 *   node tools/shots.mjs --only entry,orders
 *   node tools/shots.mjs --keep-server     复用已在跑的实例（调页面参数时省时间）
 *   node tools/shots.mjs --skip-mobile     只拍桌面页
 *   node tools/shots.mjs --help
 *
 * 前置：Node 18+（需要全局 fetch / WebSocket）、Chrome 或 Edge、项目 .venv（setup.bat）。
 */

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const IS_WIN = process.platform === 'win32';
const VENV_PY = path.join(ROOT, '.venv', IS_WIN ? 'Scripts/python.exe' : 'bin/python');
const OUT_DIR = path.join(ROOT, 'docs', 'images');
const SHOT_DIR = path.join(ROOT, '.alma', 'shots');   // 隔离工作区（已 gitignore）
const DEMO_ROOT = path.join(SHOT_DIR, 'demo_data');

const APP_PORT = 5199;    // 截图专用，避开 5000（正式）与 5001（手机端第二监听）
const MOBILE_PORT = 5198;

/** 桌面页。path 必须带结尾斜杠，否则 Flask 308 跳转、截到空白页。 */
const DESKTOP_SHOTS = [
  ['dashboard', '/'],
  ['entry', '/orders/new'],
  ['orders', '/orders/'],
  ['accounts', '/accounts/'],
  ['analytics', '/analytics/'],
  ['products', '/products/'],
  ['purchase', '/purchases/new'],
  ['settings', '/settings/'],
];

const CHROME = findChrome();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  const hit = candidates.find((p) => fs.existsSync(p));
  if (!hit && !process.env.CHROME_PATH) {
    console.error('找不到 Chrome/Edge。用 CHROME_PATH 环境变量指定可执行文件路径。');
    process.exit(1);
  }
  return process.env.CHROME_PATH || hit;
}

async function waitFor(label, probe, { timeout = 40000, interval = 400 } = {}) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    try {
      if (await probe()) return true;
    } catch (e) {
      last = e;
      // 数据根护栏抛的错必须立刻冒出来，不能被当成「还没起来」重试掉
      if (String(e.message).includes('拒绝截图')) throw e;
    }
    await sleep(interval);
  }
  throw new Error(`等待超时：${label}${last ? `（最后错误：${last.message}）` : ''}`);
}

/**
 * 起一个独立 Chrome + CDP 会话跑回调。
 * 每页独立 profile 是必须的：共用 profile 连续截图会出现多页拼贴（实测踩过）。
 */
async function withCdp(fn, { port, profile, mobile = false }) {
  const args = [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--hide-scrollbars',
    '--disable-gpu',
    '--disable-extensions',
    'about:blank',
  ];
  const proc = spawn(CHROME, args, { stdio: 'ignore' });
  try {
    await waitFor('Chrome 调试端口', async () => {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      return r.ok;
    }, { timeout: 25000, interval: 250 });

    const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const target = list.find((t) => t.type === 'page');
    if (!target) throw new Error('Chrome 没有可用的页面目标');

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
    const send = (method, params = {}) =>
      new Promise((res, rej) => {
        const mid = ++id;
        pending.set(mid, { resolve: res, reject: rej });
        ws.send(JSON.stringify({ id: mid, method, params }));
      });

    await send('Page.enable');
    await send('Runtime.enable');
    if (mobile) {
      await send('Emulation.setDeviceMetricsOverride', {
        width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
      });
      await send('Emulation.setUserAgentOverride', {
        userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 '
          + '(KHTML, like Gecko) Chrome/126 Mobile Safari/537.36',
      });
    } else {
      await send('Emulation.setDeviceMetricsOverride', {
        width: 1440, height: 900, deviceScaleFactor: 2, mobile: false,
      });
    }

    return await fn({ send });
  } finally {
    try { proc.kill(); } catch { /* 已退出 */ }
  }
}

/** 直接截成压缩 JPG，省掉 PIL 后处理这一步。 */
async function capture(send, file, quality = 86) {
  const { data } = await send('Page.captureScreenshot', {
    format: 'jpeg', quality, captureBeyondViewport: false,
  });
  fs.writeFileSync(file, Buffer.from(data, 'base64'));
  return Math.round(fs.statSync(file).size / 1024);
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
    if (code !== 0 && logName) {
      try { fs.writeFileSync(path.join(SHOT_DIR, logName), buf.join('')); } catch { /* 忽略 */ }
    }
  });
  return proc;
}

function ensureVenv() {
  if (!fs.existsSync(VENV_PY)) {
    console.error(`找不到 ${VENV_PY}\n先跑 setup.bat 准备源码虚拟环境。`);
    process.exit(1);
  }
}

/** 演示数据不随仓库分发（dist/ 被 gitignore），所以克隆后第一次跑要自己生成。 */
function ensureDemoData() {
  const db = path.join(DEMO_ROOT, 'data', 'erp.db');
  if (fs.existsSync(db)) {
    console.log('演示数据已存在，复用。');
    return;
  }
  console.log('首次运行：生成演示数据（200 条，与门店账本物理隔离）…');
  fs.mkdirSync(DEMO_ROOT, { recursive: true });
  const r = spawnSync(VENV_PY, [
    path.join(ROOT, 'packaging', 'seed_demo_data.py'),
    '--data-root', DEMO_ROOT,
    '--force',
  ], { stdio: 'inherit', cwd: ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  if (r.status !== 0) {
    console.error('演示数据生成失败，截图中止。');
    process.exit(1);
  }
}

/**
 * 硬护栏：确认服务的数据根就是隔离的演示数据根。
 * 截图是最容易被顺手对着真实账本做的事，所以这里直接拒绝，不给"小心一点"的余地。
 */
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

/** 在隔离数据根里生成一个 6 位配对码，供手机端走真实配对流程。 */
function makePairCode() {
  const code = [
    'import json, sys',
    `sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'app'))})`,
    'from erp import create_app',
    'from erp.services import mobile_pairing',
    'app = create_app()',
    'with app.test_request_context():',
    '    code, err = mobile_pairing.generate_pair_code()',
    'print(json.dumps({"code": code, "error": err}))',
  ].join('\n');

  const r = spawnSync(VENV_PY, ['-c', code], {
    cwd: ROOT,
    env: { ...process.env, ERP_DATA_ROOT: DEMO_ROOT, PYTHONIOENCODING: 'utf-8' },
    encoding: 'utf-8',
  });
  const line = String(r.stdout || '').trim().split('\n').filter(Boolean).pop();
  if (!line) throw new Error(`生成配对码失败：${r.stderr || '无输出'}`);
  const { code: pairCode, error } = JSON.parse(line);
  if (error || !pairCode) throw new Error(`生成配对码失败：${error || '空码'}`);
  return pairCode;
}

/**
 * 手机端：未配对访问 /m/ 会 302 到 /m/pair，直接截只会截到配对页。
 * 所以这里完整走一遍真实流程：起第二监听 → 填码提交 → 确认停在 /m/ → 截图。
 */
async function shootMobile() {
  const pairCode = makePairCode();
  const mobileScript = [
    'import sys',
    `sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'app'))})`,
    'from erp.mobile_app import create_mobile_app',
    `create_mobile_app().run(host="0.0.0.0", port=${MOBILE_PORT}, debug=False, use_reloader=False)`,
  ].join('\n');

  const srv = startPython(['-c', mobileScript], { ERP_DATA_ROOT: DEMO_ROOT }, 'mobile.log');
  try {
    await waitFor(`手机端服务 ${MOBILE_PORT}`, async () => {
      const r = await fetch(`http://127.0.0.1:${MOBILE_PORT}/m/pair`);
      return r.ok;
    }, { timeout: 60000 });

    const mobileFile = path.join(OUT_DIR, 'shot-mobile.jpg');
    await withCdp(async ({ send }) => {
      await send('Page.navigate', { url: `http://127.0.0.1:${MOBILE_PORT}/m/pair` });
      await sleep(2500);
      // 真实流程：填隐藏域 → 提交表单 → 服务端下发配对 cookie
      await send('Runtime.evaluate', {
        expression: `document.getElementById('pairCode').value=${JSON.stringify(pairCode)};`
          + 'document.querySelector("form").submit();',
      });
      await sleep(3500);
      const here = await send('Runtime.evaluate', {
        expression: 'location.pathname', returnByValue: true,
      });
      if (here.result.value !== '/m/') {
        throw new Error(`配对后没停在 /m/（实际 ${here.result.value}），配对码可能已过期，重跑一次。`);
      }
      await capture(send, mobileFile, 88);
    }, { port: 9420, profile: path.join(SHOT_DIR, 'chrome-mobile'), mobile: true });
    return mobileFile;
  } finally {
    try { srv.kill(); } catch { /* 已退出 */ }
  }
}

/**
 * 2x 采样再缩回目标宽度：直接 1x 采样会糊，2x 原图又太大（9 张能到 2.7MB）。
 * 用 Pillow（项目依赖）做 LANCZOS 缩放，画质和体积都合适。
 */
function downscale(files, targetWidth) {
  const code = [
    'import json, sys',
    'from PIL import Image',
    `files = json.loads(${JSON.stringify(JSON.stringify(files))})`,
    `for f in files:`,
    '    im = Image.open(f)',
    `    if im.width > ${targetWidth}:`,
    `        h = round(im.height * ${targetWidth} / im.width)`,
    `        im = im.resize((${targetWidth}, h), Image.LANCZOS)`,
    '    im.save(f, "JPEG", quality=86, optimize=True)',
    '    print(json.dumps({"f": f, "w": im.width, "h": im.height}))',
  ].join('\n');
  const r = spawnSync(VENV_PY, ['-c', code], { cwd: ROOT, encoding: 'utf-8' });
  if (r.status !== 0) {
    console.warn(`  缩放跳过（Pillow 不可用？）：${String(r.stderr || '').trim().split('\n').pop()}`);
  }
}

function parseArgs(argv) {
  const onlyIdx = argv.findIndex((a) => a === '--only');
  const onlyEq = argv.find((a) => a.startsWith('--only='));
  let only = null;
  if (onlyEq) only = onlyEq.split('=')[1].split(',').filter(Boolean);
  else if (onlyIdx >= 0 && argv[onlyIdx + 1]) only = argv[onlyIdx + 1].split(',').filter(Boolean);
  return {
    help: argv.includes('--help') || argv.includes('-h'),
    only,
    skipMobile: argv.includes('--skip-mobile'),
    keepServer: argv.includes('--keep-server'),
  };
}

async function main() {
  const opt = parseArgs(process.argv.slice(2));
  if (opt.help) {
    console.log(`重拍 README 界面截图

  node tools/shots.mjs                       重拍全部（首次自动生成演示数据）
  node tools/shots.mjs --only entry,orders   只拍指定页
  node tools/shots.mjs --keep-server         复用已在 ${APP_PORT} 跑的实例
  node tools/shots.mjs --skip-mobile         不拍手机端

输出  docs/images/shot-*.jpg
隔离  数据根固定 .alma/shots/demo_data，绝不触碰正式业务库。`);
    return;
  }

  ensureVenv();
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  ensureDemoData();

  const env = { ERP_PORT: String(APP_PORT), ERP_DATA_ROOT: DEMO_ROOT };
  const shots = DESKTOP_SHOTS.filter(([n]) => !opt.only || opt.only.includes(n));
  if (opt.only) {
    const unknown = opt.only.filter(
      (n) => !DESKTOP_SHOTS.some(([s]) => s === n) && n !== 'mobile',
    );
    if (unknown.length) {
      console.error(`不认识的页面：${unknown.join(', ')}\n可选：${DESKTOP_SHOTS.map(([n]) => n).join(', ')}, mobile`);
      process.exit(1);
    }
  }

  let app = null;
  try {
    if (!opt.keepServer) {
      console.log(`启动隔离实例（端口 ${APP_PORT}，数据根 .alma/shots/demo_data）…`);
      app = startPython([path.join('app', 'app.py')], env, 'app.log');
      await waitHealth(APP_PORT);
    } else {
      console.log(`复用 ${APP_PORT} 上的实例（跳过启动与数据根核对）。`);
    }

    const grabbed = [];
    for (let i = 0; i < shots.length; i += 1) {
      const [name, urlPath] = shots[i];
      process.stdout.write(`  ${name} … `);
      const file = path.join(OUT_DIR, `shot-${name}.jpg`);
      await withCdp(async ({ send }) => {
        await send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}${urlPath}` });
        await sleep(4000);   // 等 JS 渲染完；少于 3 秒会拍到半成品
        await capture(send, file);
      }, { port: 9401 + i, profile: path.join(SHOT_DIR, `chrome-${name}`) });
      grabbed.push(file);
      console.log('ok');
    }

    if (!opt.skipMobile && (!opt.only || opt.only.includes('mobile'))) {
      process.stdout.write('  mobile（真实配对）… ');
      grabbed.push(await shootMobile());
      console.log('ok');
    }

    // 2x 采样统一缩回目标宽度（桌面 1440 / 手机 390），否则体积会翻两倍多
    downscale(grabbed.filter((f) => !f.includes('mobile')), 1440);
    downscale(grabbed.filter((f) => f.includes('mobile')), 390);
  } finally {
    if (app) {
      try { app.kill(); } catch { /* 已退出 */ }
    }
  }

  console.log('\n完成 → docs/images/');
  let totalKb = 0;
  const made = fs.readdirSync(OUT_DIR).filter((f) => f.endsWith('.jpg'));
  for (const f of made) {
    const kb = Math.round(fs.statSync(path.join(OUT_DIR, f)).size / 1024);
    totalKb += kb;
    console.log(`  ${f.padEnd(22)} ${String(kb).padStart(4)} KB`);
  }
  console.log(`  ${'合计'.padEnd(20)} ${String(totalKb).padStart(4)} KB`);
  console.log('记得逐张看图确认没有空白页或半成品，再提交。');
}

main().then(
  () => process.exit(0),
  (e) => {
    console.error(`\n截图失败：${e.message}`);
    process.exit(1);
  },
);
