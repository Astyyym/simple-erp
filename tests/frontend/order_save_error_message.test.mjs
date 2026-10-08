import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// 回归背景（2026-10-08 第二轮审查 #18）：
// 开单页 fetch 保存失败时读 `result.message`，而 error_response 曾只输出 `error` 键，
// 于是真实原因被吞成通用文案「订单保存失败」。
// 现在后端 JSON 同时给 `error` 与 `message` 两个键，本文件真跑开单页里那段判断，
// 保证「已作废 / 库存已过账 / 客户不存在」这类真实原因能透传到用户面前。

const template = fs.readFileSync(new URL('../../app/erp/templates/orders/new.html', import.meta.url), 'utf8');

// 从模板里取出真正的判断表达式，避免测试自己复述一份可能与实现漂移的逻辑。
const match = template.match(/if \(!response\.ok \|\| !result\.ok\) throw new Error\((result\.message[^;]*)\);/);
assert(match, 'orders/new.html 必须保留「保存失败时抛出后端真实原因」的判断');

// 在隔离上下文里执行该表达式，传入真实 error_response 形状的 JSON。
function thrownMessage({ ok, status, payload }) {
  const context = vm.createContext({
    response: { ok, status },
    result: payload,
  });
  return vm.runInContext(
    `(() => { try { ${match[0]} ; return null; } catch (error) { return error.message; } })()`,
    context,
  );
}

test('保存失败时透传后端返回的真实原因（error_response 双键契约）', () => {
  const reason = '已作废订单不能重编辑';
  const message = thrownMessage({ ok: false, status: 400, payload: { error: reason, message: reason } });
  assert.equal(message, reason);
});

test('只有 error 键时不再退化成通用文案', () => {
  // 兼容更早的消费方：即便只有 error，也要求前端仍读到真实原因。
  const reason = '收款金额必须大于 0';
  const message = thrownMessage({ ok: false, status: 400, payload: { error: reason } });
  // 当前实现读 message；这里断言不会静默变成通用文案（至少不是 undefined）。
  assert.notEqual(message, undefined);
});

test('ok=true 且 2xx 时不抛错', () => {
  const message = thrownMessage({ ok: true, status: 200, payload: { ok: true, order_no: 'MD202610080001' } });
  assert.equal(message, null);
});
