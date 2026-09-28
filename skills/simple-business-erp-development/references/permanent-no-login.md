# 永久免登录（2026-07-26）

## 产品结论
- 本地单机店用：打开即进业务页，不要账号密码。
- 不保留半死不活的登录页/退出登录按钮。
- 当前产品为本机单机永久免登录；**不得将服务暴露到局域网或公网**，不能把免登录解释为用户接受了远程访问风险。任何可达端口者都可能操作数据，因此默认只绑定回环地址。

## 实现要点（源码）
| 位置 | 行为 |
|---|---|
| `app/erp/auth.py` | `is_authenticated()` 恒 True；`ensure_auth_defaults` 强制 `local_access_password_enabled=False`，不再 invent 默认密码哈希 |
| `app/erp/__init__.py` | 无 `before_request` 登录门；`auth_enabled=False`；`/health` → `auth: "disabled"` |
| `app/erp/routes/auth.py` | `/login`、`/logout` 仅重定向首页（兼容旧书签） |
| `app/erp/templates/base.html` | 顶栏无「退出登录」；状态仅「本地数据已连接」 |
| `config.json` / `CONFIG_DEFAULTS` | `local_access_password_enabled: false`，hash/username 空串 |
| 模板 | `auth/login.html` 已删除 |

## 测试对齐
- `tests/test_auth_login.py`、`test_desktop_print_auth.py`、`test_excel_export.py`：从「未登录 302→/login」改为「未登录 200 可进」。
- `conftest` 仍可设 `ERP_DISABLE_AUTH=1`（历史兼容），但产品路径即使 unset 也免登录。
- 回归：`pytest` 全量应绿；冒烟 `GET /`、`/orders/new`、`/health`。

## 交付分层（用户常拆）
- **A**：源码 + 测试（本改默认）
- **B**：README / CHANGELOG / VERSION + push
- **C**：重打 EXE

## 产品边界更新

README、CHANGELOG 和当前需求基线均已同步永久免登录规则。旧版登录门记录只作历史，不代表当前状态；修改鉴权或开放监听地址前，必须先重新确认安全需求并更新产品基线。


## 禁止回潮
- 不要为了「更安全」擅自加回登录门。
- 不要把 `ERP_DESKTOP` 当成鉴权开关；它只影响壳内打印导航。
