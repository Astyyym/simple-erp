#!/usr/bin/env node
/**
 * 一次性验收脚本（2026-10-24）：设备 ID 列 + 关闭提醒浮层。
 *
 * 为什么单独写一个而不是复用 tools/shots.mjs：shoot.mjs 会重拍 README 里的全部截图，
 * 那是发版动作；这里只要两张「改了什么」的证据图，跑在**隔离数据根**上，
 * 绝不碰门店的 data/erp.db。
 *
 * 用法：node tools/close-prompt-check.mjs
 * 产物：.alma/closecheck/settings-devices.png、close-prompt.png
 */
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = path.join(ROOT, '.venv-win', 'Scripts', 'python.exe');
const OUT = path.join(ROOT, '.alma', 'closecheck');
const DATA_ROOT = path.join(OUT, 'data_root');
const PORT = 5197;
const CDP_PORT = 9333;

fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(DATA_ROOT, { recursive: true });

const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].find((p) => fs.existsSync(p));
if (!CHROME) {
  console.error('找不到 Chrome/Edge');
  process.exit(1);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(label, probe, timeout = 60000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      if (await probe()) return;
    } catch (e) {
      /* retry */
    }
    await sleep(300);
  }
  throw new Error('等待超时：' + label);
}

// ---- 1. 起隔离实例（演示数据 + 两台设备，其一是旧记录）----
const seedScript = `
import json, os, sys
from datetime import datetime, timedelta
sys.path[:0] = ["app", "."]
from erp.db import init_db
from erp.services import accounting, mobile_pairing
init_db()
cid = accounting.create_customer("验收客户甲", opening_balance_cents=12000)
pid = accounting.create_product("验收商品 蝶阀 DN100", "个")
accounting.create_order_from_typed_rows(
    cid, "MD202610240001",
    [{"product_id": pid, "product_name": "验收商品 蝶阀 DN100", "unit": "个",
      "quantity": "2", "unit_price_yuan": "150"}],
    status="saved", order_date="2026-10-24",
)
# 一台有 uid 的新设备 + 一条无 uid 的旧记录
code, err = mobile_pairing.generate_pair_code()
assert not err, err
mobile_pairing.verify_pair_code(code, label="2201123C", platform="app", uid="a1b2c3d4e5f60718")
store = mobile_pairing.load_store()
# 让「已同步」标签有值：3 分钟前刚同步过（这样浮层文案是「不用等」，而不是全屏黄色警告）。
store["devices"][0]["last_sync_at"] = (datetime.now() - timedelta(minutes=3)).isoformat(timespec="seconds")
store["devices"].append({
    "id": "legacy0001", "token_sha256": "x" * 64, "label": "手机",
    "platform": "app", "paired_at": "2026-10-18T09:39:09",
    "last_seen_at": "", "last_ip": "192.168.10.3",
})
mobile_pairing.save_store(store)
print("seeded")
`;
const seed = spawnSync(PY, ['-c', seedScript], {
  cwd: ROOT,
  env: { ...process.env, ERP_DATA_ROOT: DATA_ROOT, PYTHONIOENCODING: 'utf-8' },
  encoding: 'utf-8',
});
if (seed.status !== 0) {
  console.error('造数失败：', seed.stdout, seed.stderr);
  process.exit(1);
}

// 打开手机访问开关，让关闭提醒有条件弹（该开关默认关）。
const enableScript = `
import sys
sys.path[:0] = ["app", "."]
from erp.config import save_config
save_config({"mobile_access_enabled": True})
print("enabled")
`;
const enabled = spawnSync(PY, ['-c', enableScript], {
  cwd: ROOT,
  env: { ...process.env, ERP_DATA_ROOT: DATA_ROOT, PYTHONIOENCODING: 'utf-8' },
  encoding: 'utf-8',
});
if (enabled.status !== 0) {
  console.error('开开关失败：', enabled.stdout, enabled.stderr);
  process.exit(1);
}

const server = spawn(PY, ['app/app.py'], {
  cwd: ROOT,
  env: {
    ...process.env,
    ERP_DATA_ROOT: DATA_ROOT,
    ERP_PORT: String(PORT),
    ERP_DISABLE_AUTH: '1',
    PYTHONIOENCODING: 'utf-8',
  },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let serverLog = '';
server.stdout.on('data', (d) => (serverLog += d.toString()));
server.stderr.on('data', (d) => (serverLog += d.toString()));

const chrome = spawn(
  CHROME,
  [
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${path.join(OUT, 'chrome-profile')}`,
    '--headless=new',
    '--hide-scrollbars',
    '--no-first-run',
    '--disable-gpu',
    '--window-size=1500,1000',
    'about:blank',
  ],
  { stdio: 'ignore' },
);

let exitCode = 0;
try {
  await waitFor('Flask', async () => (await fetch(`http://127.0.0.1:${PORT}/health`)).ok, 90000);
  await waitFor('Chrome CDP', async () => (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).ok, 30000);

  const targets = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
  const page = targets.find((t) => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.onopen = res;
    ws.onerror = rej;
  });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    }
  };
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });

  await send('Page.enable');
  await send('Runtime.enable');

  async function shoot(url, file, after, scrollTo) {
    await send('Page.navigate', { url });
    await sleep(2500);
    if (scrollTo) {
      // 设置页是一整页长表单（基本/打印/数据/手机访问/清空），设备表在很下面。
      // 滚到锚点再截，否则截到的是「公司信息」那一段。
      await send('Runtime.evaluate', {
        expression: `document.querySelector(${JSON.stringify(scrollTo)})?.scrollIntoView({block:'start'})`,
      });
      await sleep(800);
    }
    if (after) await send('Runtime.evaluate', { expression: after, awaitPromise: true });
    await sleep(1200);
    const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    fs.writeFileSync(path.join(OUT, file), Buffer.from(data, 'base64'));
    console.log('已截图', file);
  }

  // ① 设置页：设备 ID 列 + 已同步标签
  await shoot(
    `http://127.0.0.1:${PORT}/settings/`,
    'settings-devices.png',
    null,
    '#mobileDevicesTable',
  );

  // ② 关闭提醒浮层：直接调那个挂在 base.html 上的函数
  await shoot(
    `http://127.0.0.1:${PORT}/settings/`,
    'close-prompt.png',
    'window.erpClosePrompt && window.erpClosePrompt()',
  );

  // ③ 监听没起来时的文案：把 close-state 的回包改成 listening=false，再重绘一次。
  //
  // 为什么值得单独截一张：这条分支的文案如果和正常分支一样（「拿起手机去同步」），
  // 店主就会拿手机白试一遍 —— 而端口被占时手机**一定**连不上。
  await shoot(
    `http://127.0.0.1:${PORT}/settings/`,
    'close-prompt-offline.png',
    `(async () => {
       window.erpClosePrompt();
       // 拦截 fetch，返回一份「监听未启动」的状态
       const real = window.fetch;
       window.fetch = async () => new Response(JSON.stringify({
         ok: true, has_devices: true, device_count: 1, listening: false,
         listening_error: '端口 5001 被占用，可能是上次没退干净。',
         last_sync_label: '—', last_sync_seconds: null, devices: [],
         url: 'http://192.168.10.5:5001/m/', qr: ''
       }), { headers: { 'Content-Type': 'application/json' } });
       await new Promise(r => setTimeout(r, 3500));
       window.fetch = real;
       return true;
     })()`,
  );

  // ④ 状态接口本身（JSON），确认字段齐
  const state = await send('Runtime.evaluate', {
    expression: `fetch('/settings/mobile/close-state',{headers:{Accept:'application/json'}}).then(r=>r.text())`,
    awaitPromise: true,
    returnByValue: true,
  });
  const parsed = JSON.parse(state.result.value);
  console.log('close-state:', JSON.stringify({
    ok: parsed.ok,
    has_devices: parsed.has_devices,
    device_count: parsed.device_count,
    listening: parsed.listening,
    devices: parsed.devices.map((d) => ({ label: d.label, uid_short: d.uid_short, sec: d.last_sync_seconds })),
    has_qr: Boolean(parsed.qr),
    url: parsed.url,
  }));

  ws.close();
} catch (error) {
  console.error('验收失败：', error.message);
  console.error(serverLog.slice(-3000));
  exitCode = 1;
} finally {
  chrome.kill();
  server.kill();
}
process.exit(exitCode);
