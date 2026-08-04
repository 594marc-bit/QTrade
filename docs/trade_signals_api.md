# Trade Signals CRUD API

QTrade 交易信号管理接口，用于手工/脚本增删改查 `trade_signals` 表。所有接口（除 `/api/health`）均需 Bearer token 鉴权。

**Base URL:** `http://<host>:8000`

---

## 目录

- [鉴权](#鉴权)
- [端点总览](#端点总览)
- [字段说明](#字段说明)
- [价格机制：市价 vs 限价](#价格机制市价-vs-限价)
- [接口详情](#接口详情)
  - [GET /api/trade/signals — 列表查询](#get-apitradesignals--列表查询)
  - [GET /api/trade/signals/{id} — 查单条](#get-apitradesignalsid--查单条)
  - [POST /api/trade/signals — 新增信号](#post-apitradesignals--新增信号)
  - [PUT /api/trade/signals/{id} — 修改信号](#put-apitradesignalsid--修改信号)
  - [DELETE /api/trade/signals/{id} — 删除信号](#delete-apitradesignalsid--删除信号)
- [状态机说明](#状态机说明)
- [注意事项](#注意事项)
- [完整流程示例](#完整流程示例)

---

## 鉴权

所有接口（`/api/health` 除外）在请求头中携带：

```
Authorization: Bearer <API_KEY>
```

API Key 与服务端 `config.ini` 中 `[live] api_key` 一致，当前为 `Trader88888888`。

---

## 端点总览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/trade/signals` | 列表查询，支持过滤 |
| `GET` | `/api/trade/signals/{id}` | 查单条 |
| `POST` | `/api/trade/signals` | 新增信号 |
| `PUT` | `/api/trade/signals/{id}` | 修改信号（admin 模式） |
| `DELETE` | `/api/trade/signals/{id}` | 删除信号 |

已有端点（本次未改动）：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查，无需鉴权 |
| `GET` | `/api/trade/pending` | 桥接层轮询 pending 信号 |
| `PUT` | `/api/trade/{id}/status` | 桥接层回传执行状态（带状态机校验） |

---

## 字段说明

### 信号对象（trade_signals 表字段）

| 字段 | 类型 | 创建必填 | 可修改 | 说明 |
|------|------|:---:|:---:|------|
| `id` | int | — | ❌ | 自增主键，自动生成 |
| `ts_code` | string | ✅ | ✅ | 股票代码，格式 `600036.SH` |
| `action` | string | ✅ | ✅ | 买卖方向：`BUY` / `SELL` |
| `quantity` | int | ✅ | ✅ | 委托数量（股），须为 100 的整数倍 |
| `price_type` | string | ❌ | ✅ | 价格类型：`MKT`（市价，默认）/ `LIMIT`（限价） |
| `limit_price` | float | ❌ | ✅ | 限价价格，仅 `price_type=LIMIT` 时有效 |
| `scheme_name` | string | ❌ | ✅ | 策略方案名，如 `default`、`精简反转版0708` |
| `rebalance_date` | string | ✅ | ✅ | 调仓日期，格式 `YYYYMMDD` |
| `status` | string | ❌ | ✅ | 信号状态（见[状态机](#状态机说明)），创建时默认 `pending` |
| `broker_order_id` | string | ❌ | ✅ | 券商委托编号，通常由桥接层回填 |
| `filled_qty` | int | ❌ | ✅ | 已成交数量 |
| `avg_price` | float | ❌ | ✅ | 成交均价 |
| `error_msg` | string | ❌ | ✅ | 错误信息（拒绝/失败时记录） |
| `created_at` | string | — | ❌ | 创建时间，自动生成 |
| `sent_at` | string | — | ❌ | 发送时间，自动生成 |
| `filled_at` | string | — | ❌ | 成交时间，自动生成 |

### 过滤参数（仅用于 GET 列表查询）

| 参数 | 类型 | 说明 |
|------|------|------|
| `status` | string | 按状态过滤，如 `pending`、`sent`、`filled` |
| `ts_code` | string | 按股票代码过滤，如 `600036.SH` |
| `rebalance_date` | string | 按调仓日期过滤，如 `20260713` |

三个参数可组合使用。

---

## 价格机制：市价 vs 限价

信号通过 `price_type` 和 `limit_price` 两个字段控制委托价格。

### 信号层 → 桥接层 → QMT 的映射关系

| price_type | limit_price | 桥接层行为 | passorder 参数 |
|------------|:-----------:|------------|----------------|
| `MKT`（默认） | null | 市价委托 | `prType=5`（最新价）, `price=-1` |
| `LIMIT` | 有值且 >0 | 限价委托 | `prType=11`（限价）, `price=limit_price` |
| `LIMIT` | null / 0 / 负数 | 降级为市价 | `prType=5`, `price=-1` |

### 关键行为

- **市价单（MKT）**：以当前最新价成交。`limit_price` 和 `avg_price` 在信号创建时均为 null，`avg_price` 在成交后由桥接层回填。
- **限价单（LIMIT）**：以不差于 `limit_price` 的价格成交。如果桥接层收到的 `limit_price` 无效（null、0、负数），自动降级为市价单，不会报错。
- 修改已创建的 pending 信号为限价单：`PUT /api/trade/signals/{id}` 同时设置 `price_type` 和 `limit_price` 即可，桥接层下次轮询会按限价执行。

### 示例

```bash
# 市价买入 — 不指定 price_type，默认 MKT
curl -X POST http://192.168.50.229:8000/api/trade/signals \
  -H "Authorization: Bearer Trader88888888" \
  -H "Content-Type: application/json" \
  -d '{
    "ts_code": "600036.SH",
    "action": "BUY",
    "quantity": 1000,
    "rebalance_date": "20260713"
  }'

# 限价买入 — 42.50 元以下才成交
curl -X POST http://192.168.50.229:8000/api/trade/signals \
  -H "Authorization: Bearer Trader88888888" \
  -H "Content-Type: application/json" \
  -d '{
    "ts_code": "600036.SH",
    "action": "BUY",
    "quantity": 1000,
    "rebalance_date": "20260713",
    "price_type": "LIMIT",
    "limit_price": 42.50
  }'

# 将 pending 信号从市价改为限价
curl -X PUT http://192.168.50.229:8000/api/trade/signals/1 \
  -H "Authorization: Bearer Trader88888888" \
  -H "Content-Type: application/json" \
  -d '{"price_type": "LIMIT", "limit_price": 42.50}'
```

---

## 接口详情

### GET /api/trade/signals — 列表查询

返回所有信号，支持可选过滤参数。

**请求：**

```bash
# 全部信号
curl -H "Authorization: Bearer Trader88888888" \
  "http://192.168.50.229:8000/api/trade/signals"

# 只看 pending
curl -H "Authorization: Bearer Trader88888888" \
  "http://192.168.50.229:8000/api/trade/signals?status=pending"

# 组合过滤：某只股票的已发送信号
curl -H "Authorization: Bearer Trader88888888" \
  "http://192.168.50.229:8000/api/trade/signals?ts_code=600036.SH&status=sent"

# 按调仓日期过滤
curl -H "Authorization: Bearer Trader88888888" \
  "http://192.168.50.229:8000/api/trade/signals?rebalance_date=20260713"
```

**成功响应 (200):**

```json
[
  {
    "id": 1,
    "ts_code": "600036.SH",
    "action": "BUY",
    "quantity": 1000,
    "price_type": "MKT",
    "limit_price": null,
    "scheme_name": "default",
    "rebalance_date": "20260713",
    "status": "pending",
    "broker_order_id": null,
    "filled_qty": 0,
    "avg_price": null,
    "error_msg": null,
    "created_at": "2026-07-13 10:30:00",
    "sent_at": null,
    "filled_at": null
  }
]
```

空结果返回 `[]`。

---

### GET /api/trade/signals/{id} — 查单条

**请求：**

```bash
curl -H "Authorization: Bearer Trader88888888" \
  "http://192.168.50.229:8000/api/trade/signals/1"
```

**成功响应 (200):**

```json
{
  "id": 1,
  "ts_code": "600036.SH",
  "action": "BUY",
  "quantity": 1000,
  "price_type": "MKT",
  "limit_price": null,
  "scheme_name": "default",
  "rebalance_date": "20260713",
  "status": "pending",
  "broker_order_id": null,
  "filled_qty": 0,
  "avg_price": null,
  "error_msg": null,
  "created_at": "2026-07-13 10:30:00",
  "sent_at": null,
  "filled_at": null
}
```

**错误响应 (404):**

```json
{ "detail": "Signal 999 not found" }
```

---

### POST /api/trade/signals — 新增信号

创建一条新的交易信号，状态默认为 `pending`。创建后会被桥接层（`qtrade_bridge.py`）在下一次轮询时拾取执行。

**必填字段：** `ts_code`、`action`、`quantity`、`rebalance_date`

**请求：**

```bash
# 最简单的市价买入
curl -X POST http://192.168.50.229:8000/api/trade/signals \
  -H "Authorization: Bearer Trader88888888" \
  -H "Content-Type: application/json" \
  -d '{
    "ts_code": "600036.SH",
    "action": "BUY",
    "quantity": 1000,
    "rebalance_date": "20260713"
  }'

# 带可选字段的限价卖出
curl -X POST http://192.168.50.229:8000/api/trade/signals \
  -H "Authorization: Bearer Trader88888888" \
  -H "Content-Type: application/json" \
  -d '{
    "ts_code": "000001.SZ",
    "action": "SELL",
    "quantity": 500,
    "price_type": "LIMIT",
    "limit_price": 12.50,
    "scheme_name": "精简反转版0708",
    "rebalance_date": "20260713"
  }'

# 批量创建（逐条调，或见下方 Python 脚本示例）
```

**成功响应 (200):**

```json
{ "ok": true, "saved": 1 }
```

**错误响应 (400):**

```json
{ "detail": "Missing required fields: ['quantity', 'rebalance_date']" }
```

```json
{ "detail": "action must be BUY or SELL" }
```

```json
{ "detail": "quantity must be a positive integer" }
```

---

### PUT /api/trade/signals/{id} — 修改信号

修改已存在的信号字段。**不校验状态转换**（与 `PUT /api/trade/{id}/status` 不同），适合管理场景。

不允许修改 `id`、`created_at`、`sent_at`、`filled_at`。

**请求：**

```bash
# 修改数量和策略名
curl -X PUT http://192.168.50.229:8000/api/trade/signals/1 \
  -H "Authorization: Bearer Trader88888888" \
  -H "Content-Type: application/json" \
  -d '{
    "quantity": 2000,
    "scheme_name": "流动性增强版0709"
  }'

# 强制修改状态（跳过状态机校验）
curl -X PUT http://192.168.50.229:8000/api/trade/signals/1 \
  -H "Authorization: Bearer Trader88888888" \
  -H "Content-Type: application/json" \
  -d '{"status": "cancelled"}'
```

**成功响应 (200):**

```json
{ "ok": true, "signal_id": 1 }
```

**错误响应 (400):**

```json
{ "detail": "No updatable fields provided" }
```

**错误响应 (404):**

```json
{ "detail": "Signal 999 not found" }
```

---

### DELETE /api/trade/signals/{id} — 删除信号

物理删除一条信号记录。**不可恢复，谨慎操作。**

**请求：**

```bash
curl -X DELETE http://192.168.50.229:8000/api/trade/signals/1 \
  -H "Authorization: Bearer Trader88888888"
```

**成功响应 (200):**

```json
{ "ok": true, "signal_id": 1 }
```

**错误响应 (404):**

```json
{ "detail": "Signal 999 not found" }
```

---

## 状态机说明

信号有以下状态，标准流转路径为：

```
pending ──→ sent ──→ filled
  │          │
  ├──→ cancelled
  │          └──→ partial ──→ filled
  └──→ rejected          └──→ rejected
```

| 状态 | 含义 | 谁设置 |
|------|------|--------|
| `pending` | 待执行 | 创建时自动设置 |
| `sent` | 已发送到券商 | 桥接层 `passorder()` 成功后回写 |
| `filled` | 全部成交 | 桥接层查询成交后回写 |
| `partial` | 部分成交 | 桥接层查询成交后回写 |
| `rejected` | 被拒绝 | 桥接层下单/查成交失败后回写 |
| `cancelled` | 已撤销 | 人工/脚本通过 CRUD API 设置 |

**两个修改状态的端点区别：**

| | PUT /api/trade/{id}/status | PUT /api/trade/signals/{id} |
|---|---|---|
| 校验状态转换 | ✅ 严格校验 | ❌ 不校验，可跳转任意状态 |
| 自动设置时间戳 | ✅ sent_at / filled_at | ❌ 不自动设置 |
| 用途 | 桥接层自动化流程 | 人工管理/数据修正 |
| 修改其他字段 | 仅 broker_order_id 等 4 个 | 所有可修改字段 |

---

## 注意事项

1. **quantity 必须是 100 的整数倍。** A 股最小交易单位为 1 手 = 100 股。API 本身只校验正数，不校验倍数 —— 由调用方保证，否则桥接层 `int(quantity)` 后 QMT 可能拒单。

2. **信号一旦被桥接层拾取就会执行。** 创建 pending 信号后，桥接层在下一次轮询（默认 60s）时即会调用 `passorder()` 下单。如果需要取消，要在桥接层拾取前将状态改为 `cancelled`。

3. **批量创建需逐条调。** 当前 POST 端点每次创建一条。如需批量，用脚本循环调用：

   ```python
   import requests

   HEADERS = {"Authorization": "Bearer Trader88888888"}
   BASE = "http://192.168.50.229:8000"

   signals = [
       {"ts_code": "600036.SH", "action": "BUY",  "quantity": 1000, "rebalance_date": "20260713"},
       {"ts_code": "000001.SZ", "action": "SELL", "quantity": 500,  "rebalance_date": "20260713"},
       {"ts_code": "600519.SH", "action": "BUY",  "quantity": 100,  "rebalance_date": "20260713"},
   ]

   for sig in signals:
       resp = requests.post(f"{BASE}/api/trade/signals", json=sig, headers=HEADERS)
       print(resp.json())
   ```

4. **DELETE 是物理删除，不可恢复。** 如需保留记录，建议用 PUT 将状态改为 `cancelled` 代替删除。

5. **PUT 接口不校验状态转换。** 这是有意设计——管理接口需要能修正数据。正常工作流的状态更新应使用 `PUT /api/trade/{id}/status`。

6. **`rebalance_date` 是纯标识字段。** 不控制实际执行时间，桥接层只要看到 `status=pending` 就会执行。日期仅用于标记这条信号属于哪次调仓。

7. **信号和调仓之间没有事务性。** 创建信号 → 桥接层执行 → 状态回写是异步链路。如果桥接层或 QMT 未运行，信号会一直停留在 `pending`，需要自行监控。

8. **不要在生产环境直接 DELETE 已被桥接层拾取（status ≠ pending）的信号。** 删除记录会导致无法追溯历史交易。

---

## 完整流程示例

### 场景：手工触发一次调仓

```bash
BASE="http://192.168.50.229:8000"
AUTH="Authorization: Bearer Trader88888888"
DATE="20260713"

# 1. 创建买入信号
curl -X POST $BASE/api/trade/signals \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"ts_code\":\"600036.SH\",\"action\":\"BUY\",\"quantity\":1000,\"rebalance_date\":\"$DATE\"}"

curl -X POST $BASE/api/trade/signals \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"ts_code\":\"000001.SZ\",\"action\":\"BUY\",\"quantity\":1500,\"rebalance_date\":\"$DATE\"}"

# 2. 创建卖出信号
curl -X POST $BASE/api/trade/signals \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"ts_code\":\"601318.SH\",\"action\":\"SELL\",\"quantity\":800,\"rebalance_date\":\"$DATE\"}"

# 3. 查看刚创建的信号
curl -H "$AUTH" "$BASE/api/trade/signals?status=pending&rebalance_date=$DATE"

# 4. 等桥接层执行后查看状态变化
curl -H "$AUTH" "$BASE/api/trade/signals?rebalance_date=$DATE"

# 5. 如果某条信号错了，在桥接层拾取前取消
curl -X PUT $BASE/api/trade/signals/3 \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"status":"cancelled"}'
```
