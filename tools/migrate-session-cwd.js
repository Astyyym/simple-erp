#!/usr/bin/env node
/**
 * 把 DSH 会话从一个工作区目录迁移到另一个（搬项目目录后修复侧栏与会话归属）。
 *
 * 适用场景：项目文件夹被移动/改名后，DSH 侧栏里旧会话消失、或继续打开会丢历史。
 *
 * 为什么必须动两处（缺一不可）：
 *   1. **会话目录名**：`projectKey(cwd)` 由 header 的 cwd 算出
 *      （`D:\old\proj` → `--D-old-proj--`）。
 *      DSH 追加事件时走 `logPath(root, meta.cwd, id, ...)`，用的是**内存里 header 的 cwd**；
 *      只改文件不改目录名，重启后它会去新目录找不到日志，于是 open('a') **新建一个空文件**，
 *      旧历史留在旧文件里 = 会话分叉。
 *   2. **日志第一帧（header 行）里的 cwd**：工作区列表用
 *      `realpathNormalize(header.cwd) === workspace.path` 判断成员资格，不改就永远不匹配，
 *      会话在侧栏里消失。
 *   3. **会话缓存的结构性字段**（`identity.cwd` / `rows.workingDirectory`）：
 *      不跟着改，重启后会按旧 cwd 恢复"当前工作目录"，界面显示与实存不一致。
 *
 * 只重压第一帧，其余帧按字节原样保留（zstd 多帧拼接是标准的，DSH 逐帧解码）。
 * 缓存里的**历史记录**（`fileOps.path`、`commands`）一律不动 —— 那是"当时真的操作过
 * 这个文件"的事实，改写等于篡改历史，且它们不参与任何路径解析。
 *
 * 用法：
 *   node tools/migrate-session-cwd.js --old <旧路径> --new <新路径>            # dry-run
 *   node tools/migrate-session-cwd.js --old <旧路径> --new <新路径> --apply    # 真正写入
 *   node tools/migrate-session-cwd.js --rollback <备份目录>                    # 从备份恢复
 *
 * 例：
 *   node tools/migrate-session-cwd.js --old "D:\old\proj" --new "E:\new\proj"
 *
 * 前提：**必须先完全退出 DSH**（脚本会检查并拒绝在 DSH 运行时执行）。
 * 动手前请自行备份 `~/.dsh/sessions` 与 `~/.dsh/storages/session_projcache*`。
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const HOME = process.env.USERPROFILE || process.env.HOME || '';
if (!HOME) {
  console.error('无法确定用户主目录（USERPROFILE / HOME 都为空）。');
  process.exit(1);
}
const DSH_ROOT = path.join(HOME, '.dsh');
const SESSIONS_ROOT = path.join(DSH_ROOT, 'sessions');
const PROJCACHE = path.join(DSH_ROOT, 'storages', 'session_projcache', 'sessions');

/** 取 `--flag value` 形式的参数。 */
function argValue(flag) {
  const i = process.argv.indexOf(flag);
  return i !== -1 ? process.argv[i + 1] : undefined;
}

const args = process.argv.slice(2);
const APPLY = args.includes('--apply');
const ROLLBACK_DIR = argValue('--rollback');
const OLD_CWD = argValue('--old');
const NEW_CWD = argValue('--new');

if (!ROLLBACK_DIR && (!OLD_CWD || !NEW_CWD)) {
  console.error('缺少参数。用法：');
  console.error('  node tools/migrate-session-cwd.js --old <旧目录> --new <新目录> [--apply]');
  console.error('  node tools/migrate-session-cwd.js --rollback <备份目录>');
  process.exit(1);
}
if (!ROLLBACK_DIR) {
  // 必须是绝对路径：projectKey 按完整路径算目录名，相对路径会算出错的 key。
  for (const [name, value] of [['--old', OLD_CWD], ['--new', NEW_CWD]]) {
    if (!path.isAbsolute(value)) {
      console.error(`${name} 必须是绝对路径，收到: ${JSON.stringify(value)}`);
      process.exit(1);
    }
  }
}

/** 与 DSH dsh-session-persistence-jsonl 的 projectKey 逐字一致。 */
function projectKey(cwd) {
  if (cwd.length === 0) throw new Error('cannot encode an empty project path');
  let readable = '';
  let separatorRun = false;
  for (let i = 0; i < cwd.length; i++) {
    const code = cwd.charCodeAt(i);
    const ch = String.fromCharCode(code);
    if (ch === '/' || ch === '\\' || ch === ':') {
      if (!separatorRun) readable += '-';
      separatorRun = true;
    } else if (ch !== '~' && /^[A-Za-z0-9._-]$/.test(ch)) {
      readable += ch;
      separatorRun = false;
    } else {
      readable += '~' + code.toString(16).toUpperCase().padStart(4, '0');
      separatorRun = false;
    }
  }
  return `--${(readable.replace(/^-+/, '') || 'root').slice(0, 251)}--`;
}

/** 多帧 zstd 解压：按 magic 切分边界，逐帧尝试。 */
function decodeAll(raw) {
  const magic = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
  const bounds = [];
  let i = 0;
  while ((i = raw.indexOf(magic, i)) !== -1) {
    bounds.push(i);
    i += 4;
  }
  bounds.push(raw.length);
  const parts = [];
  let idx = 0;
  while (idx < bounds.length - 1) {
    let done = false;
    for (let j = idx + 1; j < bounds.length; j++) {
      try {
        const out = zlib.zstdDecompressSync(raw.subarray(bounds[idx], bounds[j]));
        parts.push(out);
        idx = j;
        done = true;
        break;
      } catch {
        /* 不是帧边界，继续扩 */
      }
    }
    if (!done) throw new Error('cannot decode zstd frame at byte ' + bounds[idx]);
  }
  return { text: Buffer.concat(parts).toString('utf8'), frameCount: parts.length, bounds };
}

/** 找到第一帧的结束偏移（解出第一行 header 所用的那一段）。 */
function firstFrameEnd(raw) {
  const magic = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
  const bounds = [];
  let i = 0;
  while ((i = raw.indexOf(magic, i)) !== -1) {
    bounds.push(i);
    i += 4;
  }
  for (let j = 1; j < bounds.length; j++) {
    try {
      zlib.zstdDecompressSync(raw.subarray(0, bounds[j]));
      return bounds[j];
    } catch {
      /* 继续 */
    }
  }
  throw new Error('cannot locate the first zstd frame');
}

function readHeader(logPath) {
  const raw = fs.readFileSync(logPath);
  const end = firstFrameEnd(raw);
  const first = zlib.zstdDecompressSync(raw.subarray(0, end)).toString('utf8');
  const nl = first.indexOf('\n');
  const headLine = nl === -1 ? first : first.slice(0, nl);
  return { raw, end, headLine, header: JSON.parse(headLine) };
}

/** 重写一个日志：只替换第一帧，其余字节原样。 */
function rewriteCwd(logPath, expectedId, dryRun) {
  const { raw, end, headLine, header } = readHeader(logPath);
  if (expectedId !== undefined && header.id !== expectedId) {
    throw new Error(`${logPath}: header id ${header.id} != dir name ${expectedId}`);
  }
  const before = decodeAll(raw);
  const beforeLines = before.text.split('\n').filter(Boolean).length;

  if (header.cwd === NEW_CWD) {
    return { skipped: true, reason: 'already new cwd', lines: beforeLines };
  }
  if (header.cwd !== OLD_CWD) {
    throw new Error(`${logPath}: unexpected cwd ${JSON.stringify(header.cwd)}`);
  }

  const newHeadLine = headLine.replace(
    JSON.stringify(OLD_CWD).slice(1, -1),
    JSON.stringify(NEW_CWD).slice(1, -1),
  );
  const newHeader = JSON.parse(newHeadLine);
  if (newHeader.cwd !== NEW_CWD) throw new Error('replacement did not take effect');

  const newFirst = zlib.zstdCompressSync(Buffer.from(newHeadLine + '\n', 'utf8'));
  const tail = raw.subarray(end);
  const composed = Buffer.concat([newFirst, tail]);

  // 自检：整份仍可解码、行数不变、header 正确
  const after = decodeAll(composed);
  const afterLines = after.text.split('\n').filter(Boolean).length;
  if (afterLines !== beforeLines) throw new Error(`line count changed ${beforeLines} -> ${afterLines}`);
  const afterFirst = after.text.slice(0, after.text.indexOf('\n'));
  if (JSON.parse(afterFirst).cwd !== NEW_CWD) throw new Error('composed header cwd wrong');

  if (!dryRun) {
    const tmp = logPath + '.cwdmigrate.tmp';
    fs.writeFileSync(tmp, composed);
    fs.renameSync(tmp, logPath);
  }
  return {
    oldBytes: raw.length,
    newBytes: composed.length,
    lines: afterLines,
    frames: after.frameCount,
  };
}

/**
 * 会话缓存里的 cwd（`identity.cwd` 与 `rows.workingDirectory.val`）。
 *
 * **只改这两个结构性字段，其余一律不动。** 这些文件里还存着几百处旧路径
 * （`contextTimeline.fileOps[].path`、`commands` 等），那是**当时真的操作过**的文件记录
 * —— 属于历史事实。全量字符串替换会把它们改写成「在新路径下发生过」，
 * 等于篡改历史记录；而且那些字段只是展示/回溯用，不参与任何路径解析。
 *
 * 用定点改值 + 写回原格式（`JSON.stringify(data, null, 2)` 与 DSH 自己的写法一致，
 * 已实测 round-trip 后的关键结构不变），不做全文字符串替换。
 *
 * 兼容两个来源：
 *   - `session_projcache`：核心会话缓存（v7）
 *   - `session_projcache_archive_manager_v2`：归档插件自己的缓存（v2）
 */
const CACHE_DIRS = [
  PROJCACHE,
  path.join(HOME, '.dsh', 'storages', 'session_projcache_archive_manager_v2', 'sessions'),
];

function updateCaches(dryRun) {
  const results = [];
  for (const dir of CACHE_DIRS) {
    if (!fs.existsSync(dir)) continue;
    for (const file of fs.readdirSync(dir)) {
      if (!file.endsWith('.json')) continue;
      const full = path.join(dir, file);
      const raw = fs.readFileSync(full, 'utf8');
      let data;
      try {
        data = JSON.parse(raw);
      } catch {
        continue; // 不是合法 JSON 就不是我们的目标
      }
      const identity = data?.record?.identity;
      const wd = data?.record?.rows?.workingDirectory;
      const idCwd = identity?.cwd;
      const wdVal = wd?.val;
      const needId = idCwd === OLD_CWD;
      const needWd = wdVal === OLD_CWD;
      if (!needId && !needWd) continue;

      if (!dryRun) {
        if (needId) identity.cwd = NEW_CWD;
        if (needWd) wd.val = NEW_CWD;
        const next = JSON.stringify(data, null, 2);
        // 自检：改完仍是合法 JSON，且两处结构字段确实是新值
        const back = JSON.parse(next);
        if (needId && back.record.identity.cwd !== NEW_CWD) throw new Error(`${file}: identity.cwd 未生效`);
        if (needWd && back.record.rows.workingDirectory.val !== NEW_CWD) throw new Error(`${file}: workingDirectory 未生效`);
        // 历史路径必须原样保留（数量不能变化）
        const lit = JSON.stringify(OLD_CWD).slice(1, -1);
        const before = raw.split(lit).length - 1;
        const after = next.split(lit).length - 1;
        if (after !== before - (needId ? 1 : 0) - (needWd ? 1 : 0)) {
          throw new Error(`${file}: 历史路径数量异常 ${before} -> ${after}`);
        }
        fs.writeFileSync(full, next, 'utf8');
      }
      results.push({
        file: path.join(path.basename(dir), file),
        fields: [needId ? 'identity.cwd' : null, needWd ? 'workingDirectory' : null].filter(Boolean).join('+'),
      });
    }
  }
  return results;
}

/** 有 DSH 在跑就不许动：内存里的旧 header 会覆盖回去，或写到已改名的路径。 */
function assertDshNotRunning() {
  const { execSync } = require('node:child_process');
  let out = '';
  try {
    out = execSync(
      'powershell -NoProfile -Command "Get-Process | Where-Object { $_.ProcessName -like \'DeepSeek*\' } | Measure-Object | Select-Object -ExpandProperty Count"',
      { encoding: 'utf8' },
    ).trim();
  } catch {
    return; // 查不到就不拦，前面已有备份与回滚
  }
  const n = Number(out);
  if (Number.isFinite(n) && n > 0) {
    console.error(`\n!!! 检测到 ${n} 个 DeepSeek Harness 进程在运行。`);
    console.error('必须先完全退出 DSH 再迁移：否则内存里的旧 header 会把改动覆盖回去，');
    console.error('或者按旧 cwd 去 open("a") 写到一个已改名的路径上。');
    console.error('退出后重新运行本脚本。');
    process.exit(2);
  }
}

function main() {
  if (ROLLBACK_DIR) {
    return rollback();
  }

  const oldDirName = projectKey(OLD_CWD);
  const newDirName = projectKey(NEW_CWD);
  const oldDir = path.join(SESSIONS_ROOT, oldDirName);
  const newDir = path.join(SESSIONS_ROOT, newDirName);

  console.log('=== 路径迁移', APPLY ? '(APPLY)' : '(DRY-RUN)', '===');
  console.log('OLD cwd :', OLD_CWD);
  console.log('NEW cwd :', NEW_CWD);
  console.log('OLD dir :', oldDirName);
  console.log('NEW dir :', newDirName);
  console.log('old dir exists:', fs.existsSync(oldDir), '| new dir exists:', fs.existsSync(newDir));

  if (APPLY) assertDshNotRunning();

  const sourceDir = fs.existsSync(oldDir) ? oldDir : (fs.existsSync(newDir) ? newDir : null);
  if (sourceDir === null) {
    console.error('两个目录都不存在，无需迁移。');
    process.exit(1);
  }

  const sessionDirs = fs.readdirSync(sourceDir).filter((d) => {
    return fs.statSync(path.join(sourceDir, d)).isDirectory();
  });
  console.log(`\n会话目录数: ${sessionDirs.length}`);

  const report = [];
  const errors = [];
  for (const name of sessionDirs) {
    const logPath = path.join(sourceDir, name, 'session.v4.jsonl.zstd');
    if (!fs.existsSync(logPath)) {
      report.push({ name, skipped: true, reason: 'no log file' });
      continue;
    }
    try {
      const r = rewriteCwd(logPath, undefined, !APPLY);
      report.push({ name, ...r });
    } catch (e) {
      errors.push({ name, message: e.message });
    }
  }

  for (const r of report) {
    console.log(' -', r.name, r.skipped ? `skip: ${r.reason}` : `ok ${r.oldBytes}->${r.newBytes}B lines=${r.lines} frames=${r.frames}`);
  }
  if (errors.length) {
    console.error('\n!!! 错误:');
    errors.forEach((e) => console.error('  ', e.name, e.message));
  }

  const cacheHits = updateCaches(!APPLY);
  console.log(`\n会话缓存命中 ${cacheHits.length} 个（只改 identity.cwd / workingDirectory）:`);
  cacheHits.forEach((c) => console.log(' -', c.file, '->', c.fields));

  // 目录改名放最后：先保证内容都对，再动位置。
  if (APPLY && errors.length === 0 && sourceDir === oldDir) {
    if (fs.existsSync(newDir)) {
      console.error('\n新目录已存在，拒绝覆盖:', newDir);
      process.exit(1);
    }
    fs.renameSync(oldDir, newDir);
    console.log('\n会话目录已改名 ->', newDirName);
  }

  if (errors.length) process.exit(1);
  console.log('\n完成。');
}

/**
 * 从备份目录恢复。备份需含 `sessions/`（旧目录名）、`session_projcache/`、`workspace.json`。
 *
 * 该目录由使用者自己用 `cp -r` / 资源管理器复制产生；本脚本不假设任何具体位置。
 */
function rollback() {
  const src = ROLLBACK_DIR;
  if (!path.isAbsolute(src)) {
    console.error(`--rollback 必须是绝对路径，收到: ${JSON.stringify(src)}`);
    process.exit(1);
  }
  if (!fs.existsSync(src)) {
    console.error('备份目录不存在:', src);
    process.exit(1);
  }
  for (const need of ['sessions', 'session_projcache', 'workspace.json']) {
    if (!fs.existsSync(path.join(src, need))) {
      console.error(`备份目录缺少 ${need}:`, src);
      process.exit(1);
    }
  }

  // 备份里的 sessions/ 就是旧目录名本身（复制时用的是整个 `--D-...--` 目录）。
  const oldProjectDir = path.join(src, 'sessions');
  const oldDirName = path.basename(oldProjectDir);
  if (!oldDirName.startsWith('--') || !oldDirName.endsWith('--')) {
    console.error('备份里的 sessions 目录名不像 DSH 项目目录:', oldDirName);
    process.exit(1);
  }
  const targetDir = path.join(SESSIONS_ROOT, oldDirName);

  assertDshNotRunning();

  console.log('=== 回滚 ===');
  console.log('备份:', src);
  console.log('恢复到:', targetDir);

  if (fs.existsSync(targetDir)) {
    console.log('目标项目目录已存在，先移走 ->', targetDir + '.pre-rollback');
    fs.rmSync(targetDir + '.pre-rollback', { recursive: true, force: true });
    fs.renameSync(targetDir, targetDir + '.pre-rollback');
  }
  fs.cpSync(oldProjectDir, targetDir, { recursive: true });
  fs.cpSync(path.join(src, 'session_projcache'), PROJCACHE, { recursive: true });
  fs.cpSync(path.join(src, 'workspace.json'), path.join(DSH_ROOT, 'storages', 'workspace.json'));

  console.log('\n恢复完成：会话日志、会话缓存、workspace.json 都已回到备份时的状态。');
  console.log('注意：`.pre-rollback` 里是回滚前的目录，确认无误后可自行删除。');
}

main();
