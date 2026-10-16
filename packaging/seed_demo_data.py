"""简单ERP 演示数据生成器 —— 绿色文件夹版「打开即用」自带的 200 笔业务数据。

设计目标：**覆盖门店日常能遇到的业务情况**。验收者打开文件夹就能在每个页面
看到对应场景的真实数据，而不是空库或几张样板单。

安全边界（硬约束）：
- 只写入 ``--data-root`` 指定的数据根；落库前先把解析结果读回核对，不猜路径。
- 目标库已存在时拒绝执行（``--force`` 才先删旧库），防止两份数据混成一锅。
- 全部通过**真实服务层**创建（不裸插 SQL），库存守恒 / 成本快照 / 单号流水
  与真实开单完全一致；生成结束跑守恒与完整性校验，不达标即非零退出。

覆盖场景：
- 销售单：正式 / 已打印 / 草稿（含重编辑后定稿）/ 零建档先卖（缺成本 → 人工
  补录）/ 选中已有商品（客户价）/ 12~20 行大单（打印分联）/ 三位小数数量 /
  作废 / 回收站删除与恢复
- 退货单：逐行关联原单（部分退 / 全额退 / 多行 / 跨月退）/ 手输 + 原单追溯
  （v3.11.0 路径）/ 草稿（含编辑定稿）/ 作废
- 拿货单：正式多行补货 / 草稿（含定稿）/ 作废（库存回冲）/ 零期初商品补货
- 退拿货单：手输（移动均价出库）/ 关联原拿货单追溯 / 旧版逐行绑定 / 草稿
  （含定稿）/ 作废 / 回收站删除与恢复
- 期初：已知成本 / 估算成本 / 零期初确认 / 总金额反推 / 期初修订
- 账款：多方式收款（含作废）/ 账务调整（含作废）/ 期初欠款客户
- 对账：多客户 / 多区间 / 多类型筛选的快照与选择

用法：
    python packaging/seed_demo_data.py --data-root <目录> [--documents 200]
        [--config-path <包根 config.json>] [--force] [--seed 20261013]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "app") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "app"))

TODAY = date.today()


def day(offset: int) -> str:
    """今天往前推 offset 天的业务日期（offset=0 即今天）。"""
    return (TODAY - timedelta(days=offset)).isoformat()


def q3(value) -> int:
    """业务数量 → 3 位小数整数（与库存层同一精度）。"""
    return int((Decimal(str(value).strip()) * 1000).to_integral_value())


def _qty_text(quantity_3dp: int) -> str:
    """3 位小数计量 → 服务层接收的数量字符串（如 5000 → "5"）。"""
    return str(Decimal(int(quantity_3dp)) / 1000)


def cents(yuan_text: str) -> int:
    """元字符串 → 分。"""
    return int((Decimal(str(yuan_text).strip()) * 100).to_integral_value())


def money(price: float) -> str:
    return f"{price:.2f}"


# --------------------------------------------------------------------------
# 主数据目录（演示用虚构资料：消防器材门店常见品名与往来对象）
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ProductSpec:
    name: str
    unit: str
    price: str                 # 默认售价（元）
    brand: str
    init_qty: int | None       # 期初数量；None = 不启用库存
    init_cost: str | None      # 期初单位成本（元）
    safety: int | None         # 安全库存
    kind: str                  # known / estimated / zero / total / none


CATALOG: list[ProductSpec] = [
    # —— 启用库存：已知成本期初 ——
    ProductSpec("干粉灭火器4kg", "具", "56.00", "桂安", 80, "38.50", 20, "known"),
    ProductSpec("干粉灭火器3kg", "具", "46.00", "桂安", 60, "31.00", 15, "known"),
    ProductSpec("干粉灭火器2kg", "具", "38.00", "浙安", 50, "25.00", 30, "known"),
    ProductSpec("推车式干粉灭火器35kg", "台", "680.00", "浙安", 12, "520.00", 3, "known"),
    ProductSpec("二氧化碳灭火器5kg", "具", "210.00", "金枪", 15, "158.00", None, "known"),
    ProductSpec("水基灭火器2L", "具", "88.00", "金枪", 20, "62.00", None, "known"),
    ProductSpec("消防水带8-65-20m", "米", "12.50", "神龙", 400, "8.60", None, "known"),
    ProductSpec("消防水带10-65-25m", "米", "15.00", "神龙", 300, "10.20", None, "known"),
    ProductSpec("消防水带13-65-20m", "米", "18.00", "神龙", 200, "12.50", None, "known"),
    ProductSpec("水枪接口DC50", "个", "22.00", "", 120, "13.50", 20, "known"),
    ProductSpec("内扣式接口KD65", "个", "26.00", "", 100, "16.00", None, "known"),
    ProductSpec("消防水枪QZ3.5/7.5", "支", "95.00", "神龙", 30, "68.00", None, "known"),
    ProductSpec("软密封闸阀DN100", "台", "420.00", "远大", 25, "310.00", None, "known"),
    ProductSpec("软密封闸阀DN150", "台", "650.00", "远大", 15, "480.00", 5, "known"),
    ProductSpec("蝶阀DN80", "台", "260.00", "远大", 20, "185.00", None, "known"),
    ProductSpec("蝶阀DN150", "台", "520.00", "远大", 12, "390.00", None, "known"),
    ProductSpec("止回阀DN100", "台", "180.00", "远大", 30, "125.00", 10, "known"),
    ProductSpec("Y型过滤器DN50", "个", "75.00", "", 40, "48.00", None, "known"),
    ProductSpec("减压阀DN65", "个", "130.00", "", 25, "88.00", None, "known"),
    ProductSpec("信号蝶阀DN100", "台", "480.00", "远大", 10, "360.00", None, "known"),
    ProductSpec("独立式感烟探测器", "只", "45.00", "北大青鸟", 8, "28.00", 3, "known"),
    ProductSpec("手动火灾报警按钮", "只", "38.00", "北大青鸟", 25, "24.00", None, "known"),
    ProductSpec("声光报警器", "只", "65.00", "北大青鸟", 18, "42.00", None, "known"),
    ProductSpec("消防应急照明灯3W", "套", "55.00", "敏华", 35, "36.00", None, "known"),
    # —— 启用库存：特殊成本来源 ——
    ProductSpec("消火栓箱800×650×240", "套", "580.00", "金枪", 0, "0.00", None, "zero"),
    ProductSpec("灭火器箱4kg×2", "个", "120.00", "", 6, "78.00", None, "estimated"),
    ProductSpec("消防水带卷盘25m", "卷", "320.00", "神龙", 5, None, None, "total"),
    ProductSpec("阻火包250型", "块", "9.50", "", 200, "5.80", 50, "estimated"),
    # —— 不启用库存：零建档 / 先卖后补场景的现实载体 ——
    ProductSpec("钢结构防火涂料20kg", "kg", "26.00", "洛科", None, None, None, "none"),
    ProductSpec("防火堵料有机高分子5kg", "kg", "18.00", "", None, None, None, "none"),
    ProductSpec("消防喷头68度下垂型", "只", "12.00", "川安", None, None, None, "none"),
    ProductSpec("防毒面具过滤式", "具", "35.00", "梅思安", None, None, None, "none"),
    ProductSpec("消防腰带", "条", "45.00", "", None, None, None, "none"),
    ProductSpec("灭火毯1m×1m", "块", "58.00", "", None, None, None, "none"),
    ProductSpec("消防斧", "把", "42.00", "", None, None, None, "none"),
    ProductSpec("通风机轴流式FT35-11", "台", "850.00", "上风", None, None, None, "none"),
    ProductSpec("消防井盖DN700", "套", "320.00", "", None, None, None, "none"),
    ProductSpec("消防桶", "只", "28.00", "", None, None, None, "none"),
    ProductSpec("沙箱1立方米", "个", "180.00", "", None, None, None, "none"),
    ProductSpec("疏散指示标志灯", "套", "42.00", "敏华", None, None, None, "none"),
]

# 从未销售过的启用商品（喂「未销售」排行）
NEVER_SOLD = {"消防水枪QZ3.5/7.5", "减压阀DN65", "声光报警器", "消防应急照明灯3W"}
# 热销加权
HOT = {"干粉灭火器4kg": 6, "干粉灭火器3kg": 4, "消防水带8-65-20m": 4, "水枪接口DC50": 3}
# 零建档自动建档：只存在于订单里的新商品名
AUTO_PRODUCT_NAMES = ["消防铲2号", "地下式消防水泵接合器", "LED吸顶声光控灯"]
# 客户专属价（设置页/开单取价覆盖）
CUSTOMER_PRICE_PAIRS = [
    ("宏伟消防工程店", "干粉灭火器4kg", "52.00"),
    ("万象购物中心", "干粉灭火器3kg", "43.50"),
    ("安宁物业管理有限公司", "消防水带8-65-20m", "11.80"),
]

CUSTOMERS: list[tuple[str, str, str, int]] = [
    # (名称, 电话, 地址, 期初余额分)
    ("宏伟消防工程店", "13812340001", "城东区解放路112号", 0),
    ("安宁物业管理有限公司", "13912340002", "高新区安宁路8号", 158000),
    ("宏图建设集团第三项目部", "13712340003", "滨江新区宏图大道66号", 0),
    ("星辰宾馆", "13612340004", "火车站广场路1号", 0),
    ("万象购物中心", "13512340005", "人民中路万象城B1层", 89600),
    ("锦华酒店", "13412340006", "湖滨南路锦华大厦", 0),
    ("实验中学", "13312340007", "学府路教育园区", 0),
    ("仁和医院", "13212340008", "健康路99号", 0),
    ("万达影城", "13112340009", "万象城5楼", 0),
    ("悦动KTV", "13012340010", "解放路地下商业街", 45800),
    ("中通快递分拨中心", "13812340011", "物流园通泽路3号", 0),
    ("老李五金店", "13712340012", "建材市场A区17号", 0),
    ("大刘建材经营部", "13612340013", "建材市场C区42号", 0),
    ("鑫源纸张厂", "13512340014", "工业园鑫源路6号", 0),
    ("东星纺织厂", "13412340015", "纺织路东星工业园", 0),
    ("西山养老院", "13312340016", "西山风景区南麓", 0),
    ("蓝天幼儿园", "13212340017", "安宁路蓝天小区旁", 0),
    ("沸腾鱼乡饭店", "13112340018", "美食街沸腾鱼乡", 0),
    ("速8酒店火车站店", "13012340019", "火车站广场路2号", 0),
    ("汇通能源加油站", "13812340020", "国道324汇通处", 0),
    ("市政绿化管理处", "13912340021", "市政府大院2号楼", 0),
    ("张强", "", "", 0),                      # 待补全客户
    ("王秀芬", "13700000000", "", 0),         # 待补全客户（缺地址）
    ("陈老板商行", "13612340024", "小商品市场东厅12号", 0),
    ("苏果超市东关店", "13512340025", "东关大街苏果超市", 0),
    ("德邦物流园区", "13412340026", "物流园德邦路1号", 0),
    ("晨光文具批发", "13312340027", "小商品市场西厅8号", 0),
    ("Andy Fire Supply", "13212340028", "No.88 Huaihai Road", 0),  # 非中文名
]

PURCHASE_NOTES = ["季度备货", "工地集中补货", "新品试销", "节前安全排查备货", "常规补货"]
SALE_NOTES = ["工地月结", "门店自提", "含送货上楼", "急单", "月结客户", "老客户回头单"]
RETURN_NOTES = ["型号发错退回", "部分退货", "质量异议退回", "多发货退回"]
PAYMENT_METHODS = ["现金", "微信", "支付宝", "银行转账"]
ADJUSTMENT_TYPES = ["优惠", "抹零", "赔损", "其他"]


# --------------------------------------------------------------------------
# 生成器
# --------------------------------------------------------------------------

class DemoDataBuilder:
    def __init__(self, data_root: Path, documents: int = 200, seed: int = 20261013):
        self.root = data_root
        self.rng = random.Random(seed)
        # 200 笔的分配比例：销售 45% / 退货 17.5% / 拿货 22.5% / 退拿货 15%
        self.plan = {
            "sale": round(documents * 0.45),
            "return": round(documents * 0.175),
            "purchase": round(documents * 0.225),
            "purchase_return": documents - round(documents * 0.45) - round(documents * 0.175) - round(documents * 0.225),
        }
        self.documents = documents
        # 运行时状态
        self.products: dict[int, ProductSpec] = {}      # pid -> spec
        self.product_by_name: dict[str, int] = {}
        self.customers: dict[int, tuple] = {}           # cid -> (name, phone, address, opening)
        self.stock: dict[int, int] = {}                 # pid -> 可用数量(3dp)
        self.sale_items: dict[int, dict] = {}           # order_item_id -> 源信息
        self.purchase_items: dict[int, dict] = {}       # purchase_item_id -> 源信息
        self.dead_orders: set[int] = set()              # 作废/软删除的销售单（不可再作退货来源）
        self.counters: dict[str, int] = {}
        self.warnings: list[str] = []

    # ---------- 基础工具 ----------

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n

    def _next_md(self, d: str) -> str:
        from erp.routes.orders import next_order_no
        from erp.db import get_db
        with get_db() as conn:
            return next_order_no(conn, d)

    def _next_nh(self, d: str) -> str:
        from erp.utils.order_numbering import next_nh_order_no
        from erp.db import get_db
        with get_db() as conn:
            return next_nh_order_no(conn, d)

    def _pick_customer(self) -> int:
        return self.rng.choice(list(self.customers))

    def _pick_stocked(self, min_units: int = 1, exclude: frozenset = frozenset()) -> int | None:
        """按热销权重选一个有足够库存的启用商品。"""
        pool: list[tuple[int, int]] = []
        for pid, spec in self.products.items():
            if spec.kind == "none" or spec.name in NEVER_SOLD or pid in exclude:
                continue
            if self.stock.get(pid, 0) < min_units * 1000:
                continue
            pool.append((pid, HOT.get(spec.name, 1)))
        if not pool:
            return None
        pids = [p for p, _ in pool]
        weights = [w for _, w in pool]
        return self.rng.choices(pids, weights=weights, k=1)[0]

    def _pick_purchasable(self) -> int:
        """拿货候选：任何启用库存的商品（含零期初，拿货正是补货路径）。"""
        pool = [pid for pid, spec in self.products.items() if spec.kind != "none"]
        return self.rng.choice(pool)

    def _return_day(self, source_date: str, min_gap: int = 0) -> str:
        """退货日期：不早于原单 + min_gap 天、不晚于今天。"""
        src = date.fromisoformat(source_date)
        earliest = src + timedelta(days=min_gap)
        if earliest > TODAY:
            earliest = TODAY
        span = (TODAY - earliest).days
        offset = self.rng.randint(0, span) if span > 0 else 0
        return (earliest + timedelta(days=offset)).isoformat()

    # ---------- 库存影响跟踪（以库为真，按单据明细回读） ----------

    def _apply_order_stock(self, order_id: int, reverse: bool = False) -> None:
        """把一张销售/退货单的库存影响叠加进跟踪表。"""
        from erp.db import get_db
        with get_db() as conn:
            order = conn.execute("SELECT order_type FROM orders WHERE id=?", (order_id,)).fetchone()
            rows = conn.execute(
                "SELECT product_id, quantity FROM order_items WHERE order_id=?", (order_id,)
            ).fetchall()
        sign = 1 if order["order_type"] == "return" else -1
        if reverse:
            sign = -sign
        for row in rows:
            pid = int(row["product_id"])
            if pid in self.stock:
                self.stock[pid] += sign * q3(row["quantity"])

    def _apply_purchase_stock(self, order_id: int, reverse: bool = False) -> None:
        from erp.db import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT product_id, quantity_3dp FROM purchase_order_items WHERE purchase_order_id=?",
                (order_id,),
            ).fetchall()
        sign = -1 if reverse else 1
        for row in rows:
            pid = int(row["product_id"])
            if pid in self.stock:
                self.stock[pid] += sign * int(row["quantity_3dp"])

    def _apply_purchase_return_stock(self, order_id: int, reverse: bool = False) -> None:
        from erp.db import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT product_id, quantity_3dp FROM purchase_return_items WHERE purchase_return_id=?",
                (order_id,),
            ).fetchall()
        sign = 1 if reverse else -1
        for row in rows:
            pid = int(row["product_id"])
            if pid in self.stock:
                self.stock[pid] += sign * int(row["quantity_3dp"])

    def _version(self, table: str, order_id: int) -> int:
        from erp.db import get_db
        with get_db() as conn:
            row = conn.execute(f"SELECT version FROM {table} WHERE id=?", (order_id,)).fetchone()
        return int(row["version"])

    # ---------- Phase 1/2：主数据 ----------

    def build_master_data(self) -> None:
        from erp.services import accounting
        for name, phone, address, opening in CUSTOMERS:
            cid = accounting.create_customer(name, phone, address, opening)
            self.customers[cid] = (name, phone, address, opening)
        self.bump("customers", len(CUSTOMERS))
        for spec in CATALOG:
            # 2026-10-15 默认价下线：建档不再写档案价。`spec.price` 降级为**脚本内部定价参数**
            # （只用于生成单据金额与客户专属价），不再进商品档案。
            pid = accounting.create_product(
                spec.name, spec.unit,
                safety_stock=spec.safety, brand=spec.brand or None,
            )
            self.products[pid] = spec
            self.product_by_name[spec.name] = pid
        self.bump("products", len(CATALOG))
        # 客户专属价
        for cust_name, prod_name, price in CUSTOMER_PRICE_PAIRS:
            cid = next(c for c, v in self.customers.items() if v[0] == cust_name)
            pid = self.product_by_name[prod_name]
            accounting.set_customer_price(cid, pid, cents(price))
        self.bump("customer_prices", len(CUSTOMER_PRICE_PAIRS))

    # ---------- Phase 3：期初库存 ----------

    def build_inventory(self) -> None:
        from erp.services.inventory import initialize_product, revise_initialization
        from erp.db import get_db
        for pid, spec in self.products.items():
            if spec.kind == "none":
                continue
            init_day = day(self.rng.randint(185, 195))
            if spec.kind == "zero":
                initialize_product(pid, "0", "0", init_day, "期初建档（零库存确认）",
                                   uuid.uuid4().hex, confirm_zero=True, cost_source="zero")
                self.stock[pid] = 0
            elif spec.kind == "total":
                # C-2：按总金额反推单位成本
                initialize_product(pid, spec.init_qty, "0", init_day, "期初建档（总金额反推）",
                                   uuid.uuid4().hex, total_cost="1200.00")
                self.stock[pid] = spec.init_qty * 1000
            else:
                initialize_product(pid, spec.init_qty, spec.init_cost, init_day, "期初建档",
                                   uuid.uuid4().hex, cost_source=spec.kind)
                self.stock[pid] = spec.init_qty * 1000
            self.bump(f"init_{spec.kind}")
        # 期初修订（盘点修正）：必须赶在首笔库存业务之前
        for name, new_qty, new_cost, reason in [
            ("阻火包250型", 180, "5.60", "盘点修正：实盘比期初少 20 块"),
            ("灭火器箱4kg×2", 8, "78.00", "盘点修正：补充遗漏 2 个"),
        ]:
            pid = self.product_by_name[name]
            with get_db() as conn:
                state = conn.execute(
                    "SELECT version FROM product_inventory_state WHERE product_id=?", (pid,)
                ).fetchone()
            revise_initialization(pid, new_qty, new_cost, reason, int(state["version"]))
            self.stock[pid] = new_qty * 1000
            self.bump("init_revisions")

    # ---------- Phase 4：拿货单 ----------

    def _purchase_rows(self, n: int) -> list[dict]:
        rows: list[dict] = []
        for _ in range(n):
            pid = self._pick_purchasable()
            spec = self.products[pid]
            if spec.unit == "米":
                qty = self.rng.choice([50, 80, 100, 120, 150, 200])
            else:
                qty = self.rng.randint(5, 30)
            base = Decimal(spec.init_cost or spec.price)
            cost = float(base * Decimal(str(self.rng.uniform(0.95, 1.12))))
            row = {"product_id": pid, "quantity": qty, "unit_cost_yuan": money(round(cost, 2))}
            if self.rng.random() < 0.25:
                # §4.3：拿货页单位可编辑、只写明细不回写档案——用「箱」覆盖两行
                row["unit"] = "箱" if spec.unit != "箱" else spec.unit
            rows.append(row)
        return rows

    def _record_purchase_items(self, order_id: int, customer_id: int) -> None:
        from erp.db import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, product_id, quantity_3dp FROM purchase_order_items WHERE purchase_order_id=?",
                (order_id,),
            ).fetchall()
        for row in rows:
            self.purchase_items[int(row["id"])] = {
                "item_id": int(row["id"]),
                "qty": int(row["quantity_3dp"]),
                "remaining": int(row["quantity_3dp"]),
                "product_id": int(row["product_id"]),
                "customer_id": customer_id,
            }

    def build_purchases(self) -> None:
        from erp.services.inventory import (
            create_purchase_order, finalize_purchase_order, void_purchase_order,
            delete_purchase_order,
        )
        saved: list[int] = []
        drafts: list[int] = []
        n_saved = self.plan["purchase"] - 5      # 5 张草稿
        for i in range(n_saved):
            d = day(self.rng.randint(8, 180))
            no = self._next_nh(d)
            cid = self._pick_customer()
            result = create_purchase_order(
                no, d, self._purchase_rows(self.rng.randint(1, 4)),
                customer_id=cid, request_key=uuid.uuid4().hex,
                source_notes=self.rng.choice(PURCHASE_NOTES), status="saved",
            )
            oid = int(result["order_id"])
            self._apply_purchase_stock(oid)
            self._record_purchase_items(oid, cid)
            saved.append(oid)
            self.bump("purchase_saved")
        # 草稿：2 张随后定稿，3 张保持草稿（其中 1 张进回收站）
        for i in range(5):
            d = day(self.rng.randint(10, 170))
            no = self._next_nh(d)
            cid = self._pick_customer()
            result = create_purchase_order(
                no, d, self._purchase_rows(self.rng.randint(1, 3)),
                customer_id=cid, request_key=uuid.uuid4().hex,
                source_notes=self.rng.choice(PURCHASE_NOTES), status="draft",
            )
            drafts.append(int(result["order_id"]))
            self.bump("purchase_draft")
        for oid in drafts[:2]:
            finalize_purchase_order(oid)
            self._apply_purchase_stock(oid)
            self._record_purchase_items(oid, self._purchase_customer_of(oid))
            self.bump("purchase_finalized")
        # 作废 4 张正式单（库存回冲）
        voided = 0
        for oid in list(saved):
            if voided >= 4:
                break
            try:
                void_purchase_order(oid, "演示：货源取消，作废回冲库存")
            except ValueError as exc:
                self.warnings.append(f"拿货作废跳过 {oid}: {exc}")
                continue
            self._apply_purchase_stock(oid, reverse=True)
            voided += 1
            self.bump("purchase_void")
        # 作废单的明细移出可退池
        from erp.db import get_db
        with get_db() as conn:
            dead_items = {
                int(r["id"])
                for r in conn.execute(
                    "SELECT i.id FROM purchase_order_items i JOIN purchase_orders o ON o.id=i.purchase_order_id "
                    "WHERE o.status='void'"
                ).fetchall()
            }
        for item_id in dead_items:
            self.purchase_items.pop(item_id, None)
        # 1 张草稿进回收站
        delete_purchase_order(drafts[-1], "演示：录错的草稿删除")
        self.bump("purchase_deleted")

    def _purchase_customer_of(self, order_id: int) -> int:
        from erp.db import get_db
        with get_db() as conn:
            row = conn.execute("SELECT customer_id FROM purchase_orders WHERE id=?", (order_id,)).fetchone()
        return int(row["customer_id"])

    # ---------- Phase 5：退拿货单 ----------

    def build_purchase_returns(self) -> None:
        from erp.services import purchase_returns as pr
        n_typed = 20
        n_trace = 4
        n_legacy = 2
        n_draft = 4
        drafts: list[int] = []
        # 手输退拿货（移动均价出库）
        for _ in range(n_typed):
            d = day(self.rng.randint(5, 175))
            pid = self._pick_stocked(min_units=1)
            if pid is None:
                continue
            spec = self.products[pid]
            qty = min(self.rng.randint(1, 6), self.stock[pid] // 1000)
            if qty <= 0:
                continue
            base = Decimal(spec.init_cost or spec.price)
            price = float(base * Decimal(str(self.rng.uniform(0.95, 1.1))))
            result = pr.create(
                customer_id=self._pick_customer(), business_date=d,
                items=[{"product_id": pid, "quantity": qty, "unit_price_yuan": money(round(price, 2))}],
                request_key=uuid.uuid4().hex, status="saved",
                notes=self.rng.choice(RETURN_NOTES),
            )
            self._apply_purchase_return_stock(int(result["order_id"]))
            self.bump("pr_typed")
        # 关联原拿货单（v3.11.0：只作追溯，手输路径）
        trace_done = 0
        for _ in range(n_trace * 6):
            if trace_done >= n_trace:
                break
            if not self.purchase_items:
                break
            item = self.rng.choice(list(self.purchase_items.values()))
            pid = item["product_id"]
            if self.stock.get(pid, 0) < 1000:
                continue
            # 找该商品 + 该往来对象的源拿货单（追溯字段要求同对象）
            from erp.db import get_db
            with get_db() as conn:
                src = conn.execute(
                    "SELECT o.id FROM purchase_orders o JOIN purchase_order_items i ON i.purchase_order_id=o.id "
                    "WHERE i.product_id=? AND o.customer_id=? AND o.status='saved' AND o.deleted_at IS NULL "
                    "ORDER BY o.id DESC LIMIT 1", (pid, item["customer_id"]),
                ).fetchone()
            if src is None:
                continue
            qty = min(self.stock[pid] // 1000, self.rng.randint(1, 4))
            if qty <= 0:
                continue
            spec = self.products[pid]
            base = Decimal(spec.init_cost or spec.price)
            price = float(base * Decimal(str(self.rng.uniform(0.95, 1.1))))
            d = day(self.rng.randint(5, 160))
            result = pr.create(
                customer_id=item["customer_id"], business_date=d,
                items=[{"product_id": pid, "quantity": qty, "unit_price_yuan": money(round(price, 2))}],
                request_key=uuid.uuid4().hex, status="saved",
                notes="演示：关联原拿货单（仅追溯）", source_order_id=int(src["id"]),
            )
            self._apply_purchase_return_stock(int(result["order_id"]))
            trace_done += 1
            self.bump("pr_trace")
        # 旧版逐行绑定路径（source_item_id 全绑）
        legacy_done = 0
        for _ in range(n_legacy * 6):
            if legacy_done >= n_legacy:
                break
            candidates = [i for i in self.purchase_items.values() if i["remaining"] >= 2000]
            if not candidates:
                break
            item = self.rng.choice(candidates)
            pid = item["product_id"]
            if self.stock.get(pid, 0) < 2000:
                continue
            from erp.db import get_db
            with get_db() as conn:
                row = conn.execute(
                    "SELECT purchase_order_id FROM purchase_order_items WHERE id=?", (item["item_id"],)
                ).fetchone()
                src = conn.execute(
                    "SELECT customer_id, business_date FROM purchase_orders WHERE id=? AND status='saved'",
                    (int(row["purchase_order_id"]),),
                ).fetchone()
            if src is None:
                continue
            qty = min(item["remaining"] // 1000, self.stock[pid] // 1000, self.rng.randint(1, 3))
            if qty <= 0:
                continue
            # 旧版路径要求退拿货日期不早于原拿货单
            src_day = date.fromisoformat(src["business_date"])
            latest = min(TODAY, src_day + timedelta(days=60))
            span = max(0, (latest - src_day).days)
            d = (src_day + timedelta(days=self.rng.randint(0, span))).isoformat()
            try:
                result = pr.create(
                    customer_id=int(src["customer_id"]), business_date=d,
                    items=[{"source_item_id": item["item_id"], "quantity": qty}],
                    request_key=uuid.uuid4().hex, status="saved",
                    notes="演示：旧版逐行绑定退拿货",
                    # 旧版路径的判定是「有原单号 **且** 所有行都绑了原行」——两个条件都要给
                    source_order_id=int(row["purchase_order_id"]),
                )
            except ValueError as exc:
                self.warnings.append(f"旧版退拿货跳过: {exc}")
                continue
            self._apply_purchase_return_stock(int(result["order_id"]))
            item["remaining"] -= qty * 1000
            legacy_done += 1
            self.bump("pr_legacy")
        # 草稿：1 张随后定稿，1 张删除后恢复，2 张保持草稿
        for i in range(n_draft):
            d = day(self.rng.randint(8, 160))
            pid = self._pick_stocked(min_units=1)
            if pid is None:
                continue
            spec = self.products[pid]
            base = Decimal(spec.init_cost or spec.price)
            price = float(base * Decimal(str(self.rng.uniform(0.95, 1.1))))
            result = pr.create(
                customer_id=self._pick_customer(), business_date=d,
                items=[{"product_id": pid,
                        "quantity": min(self.rng.randint(1, 4), self.stock[pid] // 1000),
                        "unit_price_yuan": money(round(price, 2))}],
                request_key=uuid.uuid4().hex, status="draft",
                notes="演示：退拿货草稿",
            )
            drafts.append(int(result["order_id"]))
            self.bump("pr_draft")
        if drafts:
            pr.finalize(drafts[0], expected_version=self._version("purchase_return_orders", drafts[0]))
            self._apply_purchase_return_stock(drafts[0])
            self.bump("pr_finalized")
            # 草稿[1]：删除后恢复（覆盖「恢复」路径）
            pr.delete(drafts[1], expected_version=self._version("purchase_return_orders", drafts[1]),
                      reason="演示：录错的草稿删除")
            pr.restore(drafts[1], expected_version=self._version("purchase_return_orders", drafts[1]))
            self.bump("pr_restored")
            # 草稿[2]：删除后留在回收站（覆盖「回收站中有单」状态）
            pr.delete(drafts[2], expected_version=self._version("purchase_return_orders", drafts[2]),
                      reason="演示：重复录的草稿删除")
            self.bump("pr_deleted")
        # 作废 2 张正式单
        from erp.db import get_db
        with get_db() as conn:
            saved_ids = [
                int(r["id"]) for r in conn.execute(
                    "SELECT id FROM purchase_return_orders WHERE status='saved' AND deleted_at IS NULL "
                    "ORDER BY id DESC LIMIT 12"
                ).fetchall()
            ]
        voided = 0
        for oid in saved_ids:
            if voided >= 2:
                break
            try:
                pr.void(oid, "演示：货源换回，作废回冲库存",
                        expected_version=self._version("purchase_return_orders", oid))
            except ValueError as exc:
                self.warnings.append(f"退拿货作废跳过 {oid}: {exc}")
                continue
            self._apply_purchase_return_stock(oid, reverse=True)
            voided += 1
            self.bump("pr_void")

    # ---------- Phase 6：销售单 ----------

    def _sale_rows(self, n: int, *, picker: bool = False, decimal: bool = False,
                   big: bool = False, customer_id: int | None = None) -> list[dict]:
        rows: list[dict] = []
        used: set[int] = set()
        for i in range(n):
            if decimal:
                pool = [p for p, s in self.products.items()
                        if s.unit == "米" and s.kind != "none" and p not in used
                        and self.stock.get(p, 0) > 0]
                if not pool:
                    break
                pid = self.rng.choice(pool)
                want = min(q3(self.rng.choice(["12.5", "25.5", "8.25", "15.75", "40", "33.5"])),
                           self.stock[pid])
                if want <= 0:
                    break
                qty_text = str(Decimal(want) / 1000)
            else:
                want = self.rng.randint(1, 3) if big else self.rng.randint(1, 10)
                pid = self._pick_stocked(min_units=want, exclude=frozenset(used))
                if pid is None:
                    # 没有商品吃得下这个数量：退而求其次取 1 件的
                    pid = self._pick_stocked(min_units=1, exclude=frozenset(used))
                    if pid is None:
                        break
                    want = min(want, self.stock[pid] // 1000)
                    if want <= 0:
                        break
                qty_text = str(want)
            used.add(pid)
            spec = self.products[pid]
            price = float(Decimal(spec.price) * Decimal(str(self.rng.uniform(0.96, 1.04))))
            row = {"product_name": spec.name, "unit": spec.unit,
                   "unit_price_yuan": money(round(price, 2)), "quantity": qty_text}
            if picker:
                row["product_id"] = pid
                if self.rng.random() < 0.5:
                    row.pop("unit")     # 选中路径空单位 → 回落档案单位
            rows.append(row)
        return rows

    def _record_sale_items(self, order_id: int) -> None:
        from erp.db import get_db
        with get_db() as conn:
            order = conn.execute(
                "SELECT customer_id, order_date FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT id, product_id, quantity, unit_cost_micro, cost_total_micro "
                "FROM order_items WHERE order_id=?", (order_id,)
            ).fetchall()
        for row in rows:
            if row["cost_total_micro"] is None:
                continue     # 缺成本行不能作为关联退货来源
            self.sale_items[int(row["id"])] = {
                "order_id": order_id,
                "product_id": int(row["product_id"]),
                "qty": q3(row["quantity"]),
                "returned": 0,
                "customer_id": int(order["customer_id"]),
                "order_date": order["order_date"],
            }

    def build_sales(self) -> None:
        from erp.services import accounting
        n_saved = self.plan["sale"] - 8        # 8 张草稿
        n_zero = 7                            # 零建档先卖
        n_picker = 6                          # 选中已有商品（客户价 4 + 常规 2）
        n_big = 4
        n_decimal = 2
        n_plain = n_saved - n_zero - n_picker - n_big - n_decimal
        saved_ids: list[int] = []
        draft_ids: list[int] = []
        zero_ids: list[int] = []

        # 普通正式单（手输路径，启用库存商品，带成本快照）
        for _ in range(n_plain):
            d = day(self.rng.randint(0, 165))
            cid = self._pick_customer()
            oid = accounting.create_order_from_typed_rows(
                customer_id=cid, order_no=self._next_md(d),
                rows=self._sale_rows(self.rng.randint(1, 3), customer_id=cid),
                status="saved", order_date=d, notes=self.rng.choice(SALE_NOTES),
                request_key=uuid.uuid4().hex,
            )
            self._apply_order_stock(oid)
            self._record_sale_items(oid)
            saved_ids.append(oid)
            self.bump("sale_saved")

        # 选中已有商品路径（客户价 + 空单位回落）
        pair_iter = iter(CUSTOMER_PRICE_PAIRS)
        for i in range(n_picker):
            d = day(self.rng.randint(0, 165))
            cid = self._pick_customer()
            rows = self._sale_rows(self.rng.randint(1, 2), picker=True, customer_id=cid)
            if not rows:
                continue
            if i < 4:
                try:
                    cust_name, prod_name, price = next(pair_iter)
                except StopIteration:
                    cust_name, prod_name, price = CUSTOMER_PRICE_PAIRS[i % len(CUSTOMER_PRICE_PAIRS)]
                cid = next(c for c, v in self.customers.items() if v[0] == cust_name)
                pid = self.product_by_name[prod_name]
                spec = self.products[pid]
                qty = min(self.rng.randint(2, 8), self.stock.get(pid, 0) // 1000)
                if qty <= 0:
                    continue
                rows = [{"product_id": pid, "product_name": prod_name, "unit": spec.unit,
                         "unit_price_yuan": price, "quantity": str(qty)}]
            oid = accounting.create_order_from_typed_rows(
                customer_id=cid, order_no=self._next_md(d), rows=rows,
                status="saved", order_date=d, notes="演示：选中已有商品开单",
                request_key=uuid.uuid4().hex,
            )
            self._apply_order_stock(oid)
            self._record_sale_items(oid)
            saved_ids.append(oid)
            self.bump("sale_picker")

        # 零建档 / 先卖后补（缺成本 → 后续人工补录）
        for i in range(n_zero):
            d = day(self.rng.randint(2, 160))
            cid = self._pick_customer()
            if i < 4:
                # 已存在但未启用库存的商品
                spec = self.rng.choice([s for s in CATALOG if s.kind == "none"])
                rows = [{"product_name": spec.name, "unit": spec.unit,
                         "unit_price_yuan": money(round(float(Decimal(spec.price) * Decimal("1.02")), 2)),
                         "quantity": str(self.rng.randint(1, 12))}]
            else:
                # 订单里第一次出现的全新名称 → 自动建档留痕
                name = AUTO_PRODUCT_NAMES[i - 4]
                rows = [{"product_name": name, "unit": self.rng.choice(["个", "套", "件"]),
                         "unit_price_yuan": money(round(self.rng.uniform(20, 90), 2)),
                         "quantity": str(self.rng.randint(1, 6))}]
            oid = accounting.create_order_from_typed_rows(
                customer_id=cid, order_no=self._next_md(d), rows=rows,
                status="saved", order_date=d, notes="演示：零建档先卖，后补成本",
                request_key=uuid.uuid4().hex,
            )
            zero_ids.append(oid)
            self.bump("sale_zero_cost")

        # 12~20 行大单（打印分联场景）
        for _ in range(n_big):
            d = day(self.rng.randint(1, 150))
            cid = self._pick_customer()
            rows = self._sale_rows(self.rng.randint(12, 20), big=True, customer_id=cid)
            if len(rows) < 12:
                continue
            oid = accounting.create_order_from_typed_rows(
                customer_id=cid, order_no=self._next_md(d), rows=rows,
                status="saved", order_date=d, notes="演示：大单（多联打印）",
                request_key=uuid.uuid4().hex,
            )
            self._apply_order_stock(oid)
            self._record_sale_items(oid)
            saved_ids.append(oid)
            self.bump("sale_big")

        # 三位小数数量（水带按米卖）
        for _ in range(n_decimal):
            d = day(self.rng.randint(1, 150))
            cid = self._pick_customer()
            rows = self._sale_rows(self.rng.randint(1, 2), decimal=True, customer_id=cid)
            if not rows:
                continue
            oid = accounting.create_order_from_typed_rows(
                customer_id=cid, order_no=self._next_md(d), rows=rows,
                status="saved", order_date=d, notes="演示：按米计价（三位小数）",
                request_key=uuid.uuid4().hex,
            )
            self._apply_order_stock(oid)
            self._record_sale_items(oid)
            saved_ids.append(oid)
            self.bump("sale_decimal_qty")

        # 草稿：2 张重编辑后定稿、2 张进回收站（其中 1 张恢复）、4 张保持草稿
        for i in range(8):
            d = day(self.rng.randint(1, 150))
            cid = self._pick_customer()
            rows = self._sale_rows(self.rng.randint(1, 3), customer_id=cid)
            oid = accounting.create_order_from_typed_rows(
                customer_id=cid, order_no=self._next_md(d), rows=rows,
                status="draft", order_date=d, notes="演示：销售草稿",
                request_key=uuid.uuid4().hex,
            )
            draft_ids.append(oid)
            self.bump("sale_draft")
        for oid in draft_ids[:2]:
            version = self._version("orders", oid)
            # 重编辑保号保日期保客户：读回原单头再改明细（与页面行为一致）
            from erp.db import get_db
            with get_db() as conn:
                original = conn.execute(
                    "SELECT order_no, order_date, customer_id FROM orders WHERE id=?", (oid,)
                ).fetchone()
            accounting.update_order_from_typed_rows(
                oid, int(original["customer_id"]), original["order_no"],
                self._sale_rows(self.rng.randint(1, 3)), status="saved",
                order_date=original["order_date"], notes="演示：草稿重编辑后定稿",
                expected_version=version,
            )
            self._apply_order_stock(oid)
            saved_ids.append(oid)
            self.bump("sale_reedited")

        # 8 张标记已打印
        for oid in self.rng.sample(saved_ids, k=min(8, len(saved_ids))):
            accounting.mark_order_printed(oid)
            self.bump("sale_printed")

        # 作废 3 张正式单（2 saved + 1 printed，且无退货引用）
        referenced = {item["order_id"] for item in self.sale_items.values() if item["returned"] > 0}
        voided = 0
        for oid in saved_ids:
            if voided >= 3:
                break
            if oid in referenced:
                continue
            try:
                accounting.void_order(oid, "演示：客户整单取消，作废回冲库存",
                                      expected_version=self._version("orders", oid))
            except ValueError as exc:
                self.warnings.append(f"销售作废跳过 {oid}: {exc}")
                continue
            self._apply_order_stock(oid, reverse=True)
            self.dead_orders.add(oid)
            voided += 1
            self.bump("sale_void")

        # 回收站：2 张草稿删除（1 张恢复）+ 1 张正式单作废后删除
        for oid in draft_ids[2:4]:
            accounting.delete_order(oid, "演示：录错的草稿移入回收站",
                                    expected_version=self._version("orders", oid))
            self.bump("sale_deleted")
        accounting.restore_order(draft_ids[2], expected_version=self._version("orders", draft_ids[2]))
        self.bump("sale_restored")
        for oid in saved_ids:
            if oid in self.dead_orders:
                continue
            if any(item["order_id"] == oid and item["returned"] > 0 for item in self.sale_items.values()):
                continue
            accounting.void_then_delete_order(oid, "演示：正式单列表删除（自动冲回）")
            self._apply_order_stock(oid, reverse=True)
            self.dead_orders.add(oid)
            self.bump("sale_deleted")
            break

        # 3 张零建档单人工补录历史成本
        from erp.services.cost_backfill import backfill_order_costs
        from erp.db import get_db
        for oid in zero_ids[:3]:
            with get_db() as conn:
                items = conn.execute(
                    "SELECT id, quantity FROM order_items WHERE order_id=?", (oid,)
                ).fetchall()
            entries = [{"item_id": int(r["id"]),
                        "unit_cost": money(round(self.rng.uniform(8, 60), 2))} for r in items]
            backfill_order_costs(oid, entries, "演示：补录历史销售成本")
            self.bump("backfill_items", len(entries))

    # ---------- Phase 7：退货单 ----------

    def build_returns(self) -> None:
        from erp.services import accounting
        n_source = 27        # 逐行关联原单（含部分/全额/多行/跨月）
        n_typed = 5          # 手输 + 原单追溯（v3.11.0）
        n_draft = 3
        groups = [("partial", 13), ("full", 4), ("multi", 5), ("cross_month", 5)]
        for kind, count in groups:
            done = 0
            attempts = 0
            while done < count and attempts < count * 30:
                attempts += 1
                # 可选来源：未退完、客户匹配、单仍有效
                candidates = [
                    (iid, item) for iid, item in self.sale_items.items()
                    if item["returned"] < item["qty"] and item["order_id"] not in self.dead_orders
                ]
                if not candidates:
                    break
                iid, item = self.rng.choice(candidates)
                cid = item["customer_id"]
                remaining = item["qty"] - item["returned"]   # 3dp 计量
                if kind == "full":
                    qty_3dp = remaining
                else:
                    qty_3dp = self.rng.randint(1, remaining // 1000) * 1000
                if kind == "multi":
                    same_order = [
                        (j, other) for j, other in self.sale_items.items()
                        if other["order_id"] == item["order_id"]
                        and other["returned"] < other["qty"] and j != iid
                    ]
                    rows = [{"source_item_id": iid, "quantity": _qty_text(qty_3dp)}]
                    for j, other in same_order[: self.rng.randint(1, 2)]:
                        other_remaining = other["qty"] - other["returned"]
                        rows.append({"source_item_id": j,
                                     "quantity": _qty_text(self.rng.randint(1, other_remaining // 1000) * 1000)})
                else:
                    rows = [{"source_item_id": iid, "quantity": _qty_text(qty_3dp)}]
                # 退货日期：不早于原单、不晚于今天；跨月组多跨 20~55 天
                if kind == "cross_month":
                    d = self._return_day(item["order_date"], min_gap=20)
                    if d <= item["order_date"]:
                        continue
                else:
                    d = self._return_day(item["order_date"])
                    if d < item["order_date"]:
                        d = item["order_date"]
                try:
                    oid = accounting.create_return_order_from_source(
                        customer_id=cid, order_no=self._next_md(d),
                        source_order_id=item["order_id"], rows=rows,
                        status="saved", order_date=d,
                        notes=self.rng.choice(RETURN_NOTES), request_key=uuid.uuid4().hex,
                    )
                except ValueError as exc:
                    self.warnings.append(f"关联退货跳过: {exc}")
                    continue
                self._apply_order_stock(oid)
                for row in rows:
                    self.sale_items[int(row["source_item_id"])]["returned"] += q3(row["quantity"])
                done += 1
                self.bump("return_source")

        # 手输 + 原单追溯（v3.11.0：明细不必逐行关联，单上记住原单号）
        typed_done = 0
        attempts = 0
        while typed_done < n_typed and attempts < n_typed * 30:
            attempts += 1
            sources = [oid for oid in {item["order_id"] for item in self.sale_items.values()}
                       if oid not in self.dead_orders]
            if not sources:
                break
            src = self.rng.choice(sources)
            src_item = next(item for item in self.sale_items.values() if item["order_id"] == src)
            cid = src_item["customer_id"]
            source_date = src_item["order_date"]
            d = self._return_day(source_date, min_gap=1)
            pid = self._pick_stocked(min_units=1)
            if pid is None:
                pid = src_item["product_id"]
            spec = self.products[pid]
            price = float(Decimal(spec.price) * Decimal(str(self.rng.uniform(0.97, 1.03))))
            rows = [{"product_name": spec.name, "unit": spec.unit,
                     "unit_price_yuan": money(round(price, 2)),
                     "quantity": str(self.rng.randint(1, 5))}]
            try:
                oid = accounting.create_order_from_typed_rows(
                    customer_id=cid, order_no=self._next_md(d), rows=rows,
                    status="saved", order_date=d, order_type="return",
                    source_order_id=src, notes="演示：手输退货 + 原单追溯",
                    request_key=uuid.uuid4().hex,
                )
            except ValueError as exc:
                self.warnings.append(f"手输退货跳过: {exc}")
                continue
            self._apply_order_stock(oid)
            typed_done += 1
            self.bump("return_typed")

        # 草稿：1 张关联草稿随后编辑定稿，2 张保持草稿（其中 1 张进回收站）
        draft_ids: list[int] = []
        candidates = [
            (iid, item) for iid, item in self.sale_items.items()
            if item["returned"] < item["qty"] and item["order_id"] not in self.dead_orders
        ]
        if candidates:
            iid, item = self.rng.choice(candidates)
            qty = self.rng.randint(1, (item["qty"] - item["returned"]) // 1000)
            d = self._return_day(item["order_date"], min_gap=1)
            oid = accounting.create_return_order_from_source(
                customer_id=item["customer_id"], order_no=self._next_md(d),
                source_order_id=item["order_id"],
                rows=[{"source_item_id": iid, "quantity": str(qty)}],
                status="draft", order_date=d, notes="演示：退货草稿",
                request_key=uuid.uuid4().hex,
            )
            draft_ids.append(oid)
            self.bump("return_draft")
            # 编辑草稿后定稿（改数量）
            accounting.update_return_draft_from_source(
                oid, [{"source_item_id": iid, "quantity": str(qty)}],
                status="saved", order_date=d, notes="演示：退货草稿编辑后定稿",
                expected_version=self._version("orders", oid),
            )
            self._apply_order_stock(oid)
            self.sale_items[iid]["returned"] += q3(qty)
            self.bump("return_draft_finalized")
        for _ in range(2):
            d = day(self.rng.randint(1, 150))
            cid = self._pick_customer()
            pid = self._pick_stocked(min_units=1)
            if pid is None:
                continue
            spec = self.products[pid]
            price = float(Decimal(spec.price) * Decimal(str(self.rng.uniform(0.97, 1.03))))
            oid = accounting.create_order_from_typed_rows(
                customer_id=cid, order_no=self._next_md(d),
                rows=[{"product_name": spec.name, "unit": spec.unit,
                       "unit_price_yuan": money(round(price, 2)),
                       "quantity": str(self.rng.randint(1, 4))}],
                status="draft", order_date=d, order_type="return",
                notes="演示：手输退货草稿", request_key=uuid.uuid4().hex,
            )
            draft_ids.append(oid)
            self.bump("return_draft")
        if len(draft_ids) >= 3:
            accounting.delete_order(draft_ids[-1], "演示：录错的退货草稿删除",
                                    expected_version=self._version("orders", draft_ids[-1]))
            self.bump("return_deleted")

        # 作废 2 张正式退货单
        from erp.db import get_db
        with get_db() as conn:
            saved_returns = [
                int(r["id"]) for r in conn.execute(
                    "SELECT id FROM orders WHERE order_type='return' AND status='saved' "
                    "AND deleted_at IS NULL ORDER BY id DESC LIMIT 10"
                ).fetchall()
            ]
        voided = 0
        for oid in saved_returns:
            if voided >= 2:
                break
            try:
                accounting.void_order(oid, "演示：退货协商取消，作废回冲库存",
                                      expected_version=self._version("orders", oid))
            except ValueError as exc:
                self.warnings.append(f"退货作废跳过 {oid}: {exc}")
                continue
            self._apply_order_stock(oid, reverse=True)
            voided += 1
            self.bump("return_void")

    # ---------- Phase 8：账款 ----------

    def build_accounts(self) -> None:
        from erp.services import accounting
        from erp.db import get_db
        # 收款：按客户当前余额收 40%~80%，1~2 笔
        paid = 0
        for cid in list(self.customers):
            if paid >= 25:
                break
            balance = accounting.customer_balance_cents(cid)
            if balance <= 0:
                continue
            for _ in range(self.rng.randint(1, 2)):
                if paid >= 25:
                    break
                ratio = self.rng.uniform(0.4, 0.8)
                amount = int(balance * ratio)
                if amount <= 0:
                    continue
                d = day(self.rng.randint(0, 160))
                accounting.add_payment(
                    cid, amount, payment_date=d,
                    method=self.rng.choice(PAYMENT_METHODS),
                    notes=self.rng.choice(["月结付款", "微信转账", "对公付款", "现金结清"]),
                )
                paid += 1
                self.bump("payments")
                balance -= amount
        # 作废 3 笔收款
        with get_db() as conn:
            payment_ids = [int(r["id"]) for r in conn.execute(
                "SELECT id FROM payments WHERE status='active' ORDER BY id DESC LIMIT 6"
            ).fetchall()]
        for pid in payment_ids[:3]:
            accounting.void_payment(pid, "演示：收款录错，作废后重收")
            self.bump("payments_void")
        # 账务调整 8 笔（含 2 笔作废）
        adjustment_ids: list[int] = []
        for i in range(8):
            cid = self._pick_customer()
            kind = ADJUSTMENT_TYPES[i % len(ADJUSTMENT_TYPES)]
            if kind == "抹零":
                amount = -self.rng.randint(1, 99)
            elif kind in ("优惠", "赔损"):
                amount = -self.rng.randint(100, 3000)
            else:
                amount = self.rng.choice([-1, 1]) * self.rng.randint(50, 1500)
            aid = accounting.add_adjustment(
                cid, amount, f"演示：{kind}", adjustment_type=kind)
            adjustment_ids.append(aid)
            self.bump("adjustments")
        for aid in adjustment_ids[:2]:
            accounting.void_adjustment(aid, "演示：调整录错，作废")
            self.bump("adjustments_void")

    # ---------- Phase 9：对账快照 ----------

    def build_reconciliation(self) -> None:
        from erp.services.reconciliation import (
            create_reconciliation_snapshot, update_reconciliation_selection,
            toggle_reconciliation_selection,
        )
        from erp.db import get_db
        with get_db() as conn:
            busy = [
                int(r["customer_id"]) for r in conn.execute(
                    "SELECT customer_id, COUNT(*) c FROM orders WHERE deleted_at IS NULL "
                    "GROUP BY customer_id ORDER BY c DESC LIMIT 6"
                ).fetchall()
            ]
        plans = [
            ("", "", "all"),
            (day(120), day(60), "all"),
            (day(60), "", "sale"),
            ("", "", "customer_return"),
            (day(90), day(30), "purchase"),
            (day(30), "", "all"),
        ]
        for idx, cid in enumerate(busy):
            start, end, type_filter = plans[idx % len(plans)]
            sid = create_reconciliation_snapshot(cid, start, end, type_filter)
            self.bump("snapshots")
            with get_db() as conn:
                scope = json.loads(conn.execute(
                    "SELECT scope_json FROM reconciliation_snapshots WHERE id=?", (sid,)
                ).fetchone()["scope_json"])
            if len(scope) >= 3 and idx % 3 == 0:
                update_reconciliation_selection(
                    sid, mode="explicit", selected_ids=scope[: max(1, len(scope) // 2)])
                self.bump("snapshot_selections")
            elif len(scope) >= 2 and idx % 3 == 1:
                update_reconciliation_selection(sid, mode="all", excluded_ids=[scope[-1]])
                self.bump("snapshot_selections")
            elif scope:
                toggle_reconciliation_selection(sid, scope[0], False)
                self.bump("snapshot_selections")

    # ---------- 校验 ----------

    def verify(self) -> dict:
        from erp.db import get_db, integrity_check
        problems: list[str] = []
        with get_db() as conn:
            sales = conn.execute("SELECT COUNT(*) c FROM orders WHERE order_type='sale'").fetchone()["c"]
            returns = conn.execute("SELECT COUNT(*) c FROM orders WHERE order_type='return'").fetchone()["c"]
            purchases = conn.execute("SELECT COUNT(*) c FROM purchase_orders").fetchone()["c"]
            prs = conn.execute("SELECT COUNT(*) c FROM purchase_return_orders").fetchone()["c"]
            total = sales + returns + purchases + prs
            if total != self.documents:
                problems.append(f"业务单据总数 {total} != 目标 {self.documents}")
            for key, actual in (("sale", sales), ("return", returns),
                                ("purchase", purchases), ("purchase_return", prs)):
                if actual != self.plan[key]:
                    problems.append(f"{key} 数量 {actual} != 计划 {self.plan[key]}")
            # 库存守恒：过账流水之和必须等于当前状态
            rows = conn.execute(
                """
                SELECT s.product_id, s.quantity_3dp, s.cost_total_micro,
                       COALESCE(SUM(p.quantity_delta_3dp), 0) AS pq,
                       COALESCE(SUM(p.cost_delta_micro), 0) AS pc
                FROM product_inventory_state s
                LEFT JOIN inventory_postings p ON p.product_id = s.product_id
                GROUP BY s.product_id
                """
            ).fetchall()
            for row in rows:
                if int(row["quantity_3dp"]) != int(row["pq"]) or int(row["cost_total_micro"]) != int(row["pc"]):
                    problems.append(f"库存不守恒: 商品 {row['product_id']}")
            # 跟踪表与库一致
            for pid, qty in self.stock.items():
                state = conn.execute(
                    "SELECT quantity_3dp FROM product_inventory_state WHERE product_id=?", (pid,)
                ).fetchone()
                if state is None or int(state["quantity_3dp"]) != qty:
                    problems.append(f"库存跟踪不一致: 商品 {pid}")
            # 场景覆盖硬指标
            coverage = {
                "sale_void": conn.execute(
                    "SELECT COUNT(*) c FROM orders WHERE order_type='sale' AND status='void'"
                ).fetchone()["c"],
                "return_void": conn.execute(
                    "SELECT COUNT(*) c FROM orders WHERE order_type='return' AND status='void'"
                ).fetchone()["c"],
                "purchase_void": conn.execute(
                    "SELECT COUNT(*) c FROM purchase_orders WHERE status='void'"
                ).fetchone()["c"],
                "pr_void": conn.execute(
                    "SELECT COUNT(*) c FROM purchase_return_orders WHERE status='void'"
                ).fetchone()["c"],
                "recycled_orders": conn.execute(
                    "SELECT COUNT(*) c FROM orders WHERE deleted_at IS NOT NULL"
                ).fetchone()["c"],
                "recycled_purchases": conn.execute(
                    "SELECT COUNT(*) c FROM purchase_orders WHERE deleted_at IS NOT NULL"
                ).fetchone()["c"],
                "recycled_prs": conn.execute(
                    "SELECT COUNT(*) c FROM purchase_return_orders WHERE deleted_at IS NOT NULL"
                ).fetchone()["c"],
                "linked_returns": conn.execute(
                    "SELECT COUNT(*) c FROM orders WHERE order_type='return' AND source_order_id IS NOT NULL"
                ).fetchone()["c"],
                "missing_cost_sales": conn.execute(
                    "SELECT COUNT(DISTINCT order_id) c FROM order_items oi JOIN orders o ON o.id=oi.order_id "
                    "WHERE o.order_type='sale' AND o.status IN ('saved','printed') AND oi.cost_total_micro IS NULL"
                ).fetchone()["c"],
                "backfilled_items": conn.execute(
                    "SELECT COUNT(*) c FROM order_items WHERE cost_source='backfill'"
                ).fetchone()["c"],
                "payments": conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"],
                "adjustments": conn.execute("SELECT COUNT(*) c FROM adjustments").fetchone()["c"],
                "snapshots": conn.execute("SELECT COUNT(*) c FROM reconciliation_snapshots").fetchone()["c"],
                "audit_logs": conn.execute("SELECT COUNT(*) c FROM audit_logs").fetchone()["c"],
                "postings": conn.execute("SELECT COUNT(*) c FROM inventory_postings").fetchone()["c"],
            }
            required = {
                "sale_void": 1, "return_void": 1, "purchase_void": 1, "pr_void": 1,
                "recycled_orders": 2, "recycled_purchases": 1, "recycled_prs": 1,
                "linked_returns": 20, "missing_cost_sales": 3, "backfilled_items": 3,
                "payments": 15, "adjustments": 6, "snapshots": 4, "audit_logs": 100,
                "postings": 50,
            }
            for key, minimum in required.items():
                if coverage[key] < minimum:
                    problems.append(f"场景覆盖不足: {key}={coverage[key]} < {minimum}")
        integrity = integrity_check()
        if integrity != "ok":
            problems.append(f"integrity_check: {integrity}")
        manifest = {
            "documents": {"sale": sales, "return": returns,
                          "purchase": purchases, "purchase_return": prs, "total": total},
            "coverage": coverage,
            "counters": self.counters,
            "warnings": self.warnings,
            "problems": problems,
        }
        return manifest


# --------------------------------------------------------------------------
# 演示配置（让打印件完整、工作台不弹「打印信息缺失」引导）
# --------------------------------------------------------------------------

DEMO_CONFIG = {
    "shop_name": "演示消防器材店",
    "app_name": "演示消防器材店",
    "print_order_phone": "0571-88888888",
    "print_order_address": "浙江省杭州市演示区消防路1号",
}


def apply_demo_config(config_path: Path) -> None:
    """把演示用店铺/打印信息写进包根 config.json（文件必须已存在）。"""
    if not config_path.exists():
        raise FileNotFoundError(f"包根配置不存在: {config_path}")
    os.environ["ERP_CONFIG_PATH"] = str(config_path)
    from erp.config import save_config, load_config
    save_config(DEMO_CONFIG, base=load_config())


# --------------------------------------------------------------------------
# 说明文件
# --------------------------------------------------------------------------

def write_notes(package_root: Path, manifest: dict) -> Path:
    docs = manifest["documents"]
    cov = manifest["coverage"]
    lines = [
        "简单ERP 演示数据说明（绿色文件夹版）",
        "=" * 44,
        "",
        f"本文件夹自带 {docs['total']} 笔演示业务数据（业务日期跨约半年，截止 {TODAY.isoformat()}），",
        "覆盖门店日常能遇到的业务情况，打开即可逐页点选体验。",
        "",
        "数据位置：本文件夹下的 demo_data\\（数据库 / 备份 / 日志都在里面），",
        "与「文档\\简单ERP数据」相互独立；整个文件夹可随意拷贝，数据随行。",
        "",
        "各模块能看到什么",
        "-" * 44,
        "· 工作台：今日净额 / 销售退货笔数 / 最近业务",
        "· 开单（销售 / 退货 / 拿货 / 退拿货）：客户、商品、单位候选全部来自演示数据；",
        "  退货单「原销售单」输入框可检索到演示销售单，选中后可一键带入商品行",
        "· 单据管理：四类单据混合筛选、分页、类型多选、「只看缺成本」筛选",
        "· 基础资料：28 个往来对象、40 个商品（含未启用库存 / 安全库存 / 品牌）",
        "· 商品列表：当前库存 / 移动平均成本 / 库存金额 / 库存状态（告急、缺货）",
        "· 数据分析：约半年流水热力图、利润与成本诊断、产品排行、往来对账",
        "· 账款：客户余额、收款 / 调整流水、汇总 PDF",
        "· 回收站：作废与删除的单据、草稿回收与恢复",
        "",
        "场景覆盖（按实际生成结果）",
        "-" * 44,
        f"· 销售单 {docs['sale']} 笔：正式 / 已打印 / 草稿 / 重编辑定稿 / 大单多联 / 按米三位小数 /",
        f"  零建档缺成本 / 作废 {cov['sale_void']} 笔 / 回收站删除恢复",
        f"· 退货单 {docs['return']} 笔：逐行关联原单（部分退 / 全额退 / 多行 / 跨月退）、",
        f"  手输 + 原单追溯、草稿编辑定稿、作废 {cov['return_void']} 笔",
        f"· 拿货单 {docs['purchase']} 笔：正式补货 / 草稿定稿 / 作废回冲 {cov['purchase_void']} 笔 / 回收站",
        f"· 退拿货单 {docs['purchase_return']} 笔：手输移动均价出库 / 关联原拿货单追溯 / 旧版逐行绑定 /",
        f"  草稿定稿与恢复 / 作废 {cov['pr_void']} 笔",
        "· 期初：已知成本 / 估算成本 / 零期初确认 / 总金额反推 / 盘点修订",
        f"· 账款：收款 {cov['payments']} 笔（多方式，含作废）、调整 {cov['adjustments']} 笔（含作废）",
        f"· 对账快照 {cov['snapshots']} 份；缺成本销售单 {cov['missing_cost_sales']} 张、",
        f"  人工补录成本 {cov['backfilled_items']} 行；审计日志 {cov['audit_logs']} 条",
        "",
        "重置方式",
        "-" * 44,
        "· 想从零开始：设置 → 清空所有数据（会自动先备份到 demo_data\\backups）",
        "· 想彻底移除演示数据：关闭程序后删除 demo_data 文件夹，重启即为空库首次运行",
        "",
    ]
    path = package_root / "演示数据说明.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成简单ERP演示数据（绿色文件夹版）")
    parser.add_argument("--data-root", required=True, help="演示数据根目录（包内 demo_data）")
    parser.add_argument("--documents", type=int, default=200, help="业务单据总数（默认 200）")
    parser.add_argument("--seed", type=int, default=20261013, help="随机种子（固定可复现）")
    parser.add_argument("--force", action="store_true", help="目标库已存在时先删除再生成")
    parser.add_argument("--config-path", default=None,
                        help="包根 config.json；给出则同时写入演示店铺/打印信息")
    args = parser.parse_args(argv)

    root = Path(args.data_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    db_file = root / "data" / "erp.db"
    if db_file.exists():
        if not args.force:
            print(f"拒绝执行：目标库已存在（{db_file}）。确认要重建请加 --force。")
            return 2
        for suffix in ("", "-wal", "-shm"):
            stale = Path(str(db_file) + suffix)
            if stale.exists():
                stale.unlink()

    # 数据根隔离：先设置、再导入 erp、再读回核对（不猜路径）。
    os.environ["ERP_DATA_ROOT"] = str(root)
    os.environ.pop("ERP_CONFIG_PATH", None)
    os.environ.pop("ERP_LOCATION_FILE", None)
    from erp.config import data_root as resolve_data_root
    from erp.db import init_db
    resolved = resolve_data_root()
    if resolved != root:
        print(f"拒绝执行：解析出的数据根 {resolved} 与目标 {root} 不一致。")
        return 2

    init_db()
    builder = DemoDataBuilder(root, documents=args.documents, seed=args.seed)
    builder.build_master_data()
    builder.build_inventory()
    builder.build_purchases()
    builder.build_purchase_returns()
    builder.build_sales()
    builder.build_returns()
    builder.build_accounts()
    builder.build_reconciliation()
    manifest = builder.verify()

    # WAL 归档前检查点：确保 data/erp.db 自洽、不依赖 -wal/-shm 边车文件
    import sqlite3
    checkpoint = sqlite3.connect(db_file)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_file) + suffix)
        if sidecar.exists():
            manifest.setdefault("problems", []).append(f"存在 WAL 边车文件: {sidecar}")

    if args.config_path:
        apply_demo_config(Path(args.config_path).expanduser().resolve())

    package_root = root.parent if root.name == "demo_data" else root
    notes = write_notes(package_root, manifest)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\n说明文件: {notes}")
    if manifest["problems"]:
        print("\n校验未通过：")
        for problem in manifest["problems"]:
            print(f"  - {problem}")
        return 1
    print("\n演示数据生成完毕，校验全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
