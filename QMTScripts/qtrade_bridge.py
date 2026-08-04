#coding:gbk
"""
qtrade_bridge.py — 大QMT 桥接策略

通过 run_time 定时轮询 Mac API → passorder 下单 → HTTP 回传状态。
不包含策略逻辑，QMT 只做执行。

部署：
  1. 修改下方 MAC_HOST 和 API_KEY
  2. 复制到 QMT 策略目录，策略管理器加载
  3. 运行模式选"实盘"或"模拟"均可
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# ============================================================
# 部署配置
# ============================================================

MAC_HOST = "http://192.168.50.229:8000"
API_KEY = "Trader88888888"
POLL_SECONDS = 60

# ============================================================
# 全局状态（不用 ContextInfo 存，避免 bar 回滚）
# ============================================================

class G:
    accID = ""
    poll_count = 0
    processed = set()  # 本轮已处理的信号 ID，防止重复下单
    active_remarks = {}  # ts_code → remark，记录已发送委托的备注，便于撤单匹配

g = G()

# ============================================================
# HTTP 通信
# ============================================================

def _http_get(url, timeout=10):
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None


def _http_put(url, body, timeout=10):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": "Bearer " + API_KEY,
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ============================================================
# QMT 入口
# ============================================================

def init(ContextInfo):
    # ContextInfo.accID 在实盘模式不存在，优先用 QMT 内置全局 account
    acc_id = getattr(ContextInfo, 'accID', '') or ''
    if not acc_id:
        try:
            acc_id = str(account)
        except:
            pass
    g.accID = acc_id if acc_id else 'test'
    g.poll_count = 0

    print("[qtrade_bridge] ======== 初始化 ========")
    print("[qtrade_bridge] Mac:  {0}".format(MAC_HOST))
    print("[qtrade_bridge] Poll: {0}s".format(POLL_SECONDS))
    print("[qtrade_bridge] Acc:  {0}".format(g.accID))

    # 健康检查
    result = _http_get(MAC_HOST + "/api/health")
    if result and result.get("status") == "ok":
        print("[qtrade_bridge] Health OK, pending: {0}".format(
            result.get("pending_signals", 0)))
    else:
        print("[qtrade_bridge] WARNING: Health check failed")

    # 鉴权检查
    pending = _http_get(MAC_HOST + "/api/trade/pending")
    if pending is not None:
        print("[qtrade_bridge] Auth OK, signals: {0}".format(len(pending)))
    else:
        print("[qtrade_bridge] ERROR: Auth failed — check API_KEY")

    # 动态计算下次触发时间（避免过去时间导致立即触发死循环）
    next_run = (datetime.now() + timedelta(seconds=POLL_SECONDS)).strftime('%Y-%m-%d %H:%M:%S')
    ContextInfo.run_time("poll_signals", "{0}nSecond".format(POLL_SECONDS), next_run)
    print("[qtrade_bridge] run_time registered, next: {0}".format(next_run))


def handlebar(ContextInfo):
    pass


# ============================================================
# 定时轮询 & 下单
# ============================================================

def _is_trading_time():
    """判断当前是否在 A 股交易时段（周一至周五 9:30-15:00）。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    tm = now.strftime("%H%M%S")
    return "093000" <= tm <= "150000"


def poll_signals(ContextInfo):
    # ---- 立即重新注册（run_time 是一次性的，最早续上防止丢失） ----
    next_run = (datetime.now() + timedelta(seconds=POLL_SECONDS)).strftime('%Y-%m-%d %H:%M:%S')
    ContextInfo.run_time("poll_signals", "{0}nSecond".format(POLL_SECONDS), next_run)

    g.poll_count += 1
    result = ""  # 本轮结果描述

    try:
        if not _is_trading_time():
            result = "非交易时段，跳过"
            return

        signals = _http_get(MAC_HOST + "/api/trade/pending")

        if signals is None:
            result = "GET failed"
            return

        if not signals:
            result = "无待处理信号"
            return

        new_signals = [s for s in signals if s.get("id") not in g.processed]

        if not new_signals:
            result = "{0} 个信号已处理过".format(len(signals))
            return

        for sig in new_signals:
            _execute(ContextInfo, sig)
            g.processed.add(sig.get("id"))

        result = "{0} 个信号, {1} 个新, 已执行".format(len(signals), len(new_signals))

    except Exception as e:
        result = "异常: {0}".format(e)

    finally:
        print("[qtrade_bridge] poll#{0} → {1}".format(g.poll_count, result))


def _execute(ContextInfo, sig):
    sig_id = sig.get("id", "?")
    ts_code = sig.get("ts_code", "")
    action = sig.get("action", "")
    quantity = sig.get("quantity", 0)
    price_type = sig.get("price_type", "MKT")
    limit_price = sig.get("limit_price")

    # ---- 撤单 ----
    if action == "CANCEL":
        _cancel_orders(ContextInfo, sig)
        return

    if action not in ("BUY", "SELL") or quantity <= 0:
        return

    try:
        # passorder(opType, orderType, accountID, stockCode, prType,
        #           price, volume, strategyName, quickTrade, remark, ContextInfo)
        # opType: 23=买入 24=卖出
        # orderType: 1101=按股数
        # prType: 5=最新价(市价), 11=限价
        # quickTrade: 2=立即交易（不等待K线完成）

        op_type = 23 if action == "BUY" else 24

        if price_type == "LIMIT" and limit_price is not None and limit_price > 0:
            pr_type = 11      # 限价
            price = float(limit_price)
        else:
            pr_type = 5       # 最新价（市价）
            price = -1

        # remark: 优先用信号里的 remark，否则用 rebalance_date，最后用 sig_id 兜底
        remark = sig.get("remark", "") or "rebalance_" + str(sig.get("rebalance_date", "")) or "sig_" + str(sig_id)

        passorder(
            op_type,
            1101,
            g.accID,
            ts_code,
            pr_type,
            price,
            int(quantity),
            "qtrade_bridge",
            2,
            remark,
            ContextInfo,
        )

        # 记录 remark，供撤单时按方式2匹配
        g.active_remarks[ts_code] = remark

        # 下单日志：显示价格类型和价格，方便在 QMT 界面判断
        price_desc = "限价{0}".format(price) if pr_type == 11 else "市价"
        print("[qtrade_bridge] [{0}] {1} {2} {3}股 {4} remark={5} OK".format(
            sig_id, ts_code, action, quantity, price_desc, remark))

        _update_status(sig_id, "sent", broker_order_id=str(sig_id))

    except Exception as e:
        print("[qtrade_bridge] [{0}] FAIL: {1}".format(sig_id, e))
        _update_status(sig_id, "rejected", error_msg=str(e))


def _cancel_orders(ContextInfo, sig):
    """
    撤单：优先按 remark 匹配（方式2），未匹配则撤同股票所有可撤委托（方式1）。
    可撤单状态：50=已报, 51=待报, 52=部分成交, 55=已报待撤
    """
    sig_id = sig.get("id", "?")
    ts_code = sig.get("ts_code", "")
    order_remark = sig.get("order_remark", "")

    CANCELABLE = {50, 51, 52, 55}

    try:
        orders = get_trade_detail_data(g.accID, 'STOCK', 'order')
        if not orders:
            print("[qtrade_bridge] [{0}] CANCEL 无委托记录".format(sig_id))
            _update_status(sig_id, "cancel_failed", error_msg="无委托记录")
            return

        # 筛选同股票代码 + 可撤单状态
        pending = []
        for o in orders:
            code = o.m_strInstrumentID
            if hasattr(o, 'm_strExchangeID') and o.m_strExchangeID:
                code += '.' + o.m_strExchangeID
            if code == ts_code and o.m_nOrderStatus in CANCELABLE:
                pending.append(o)

        if not pending:
            print("[qtrade_bridge] [{0}] CANCEL {1} 无可撤委托".format(sig_id, ts_code))
            _update_status(sig_id, "cancel_failed", error_msg="无可撤委托")
            return

        target = None

        # 方式2（优先）：按 remark 精确匹配
        if not order_remark:
            # 如果信号没给 remark，尝试用之前下单时记录的 remark
            order_remark = g.active_remarks.get(ts_code, "")

        if order_remark:
            for o in pending:
                if getattr(o, 'm_strRemark', '') == order_remark:
                    target = o
                    break
            if target:
                print("[qtrade_bridge] [{0}] CANCEL remark匹配: {1}".format(sig_id, order_remark))
            else:
                print("[qtrade_bridge] [{0}] CANCEL remark={1} 未匹配，退回方式1".format(
                    sig_id, order_remark))

        # 方式1（兜底）：撤该股票所有可撤委托
        if not target:
            print("[qtrade_bridge] [{0}] CANCEL 撤{1}全部可撤委托({2}笔)".format(
                sig_id, ts_code, len(pending)))
            cancelled = 0
            for o in pending:
                if _do_cancel(o, ContextInfo, sig_id):
                    cancelled += 1
            _update_status(sig_id, "cancelled", broker_order_id="bulk_{0}".format(cancelled))
            return

        # 单笔撤单
        if _do_cancel(target, ContextInfo, sig_id):
            _update_status(sig_id, "cancelled", broker_order_id=str(target.m_nTaskId))
        else:
            _update_status(sig_id, "cancel_failed", error_msg="撤单失败")

    except Exception as e:
        print("[qtrade_bridge] [{0}] CANCEL FAIL: {1}".format(sig_id, e))
        _update_status(sig_id, "cancel_failed", error_msg=str(e))


def _do_cancel(order, ContextInfo, sig_id):
    """执行单笔撤单，返回 True/False"""
    task_id = str(order.m_nTaskId)
    ts_code = order.m_strInstrumentID
    if hasattr(order, 'm_strExchangeID') and order.m_strExchangeID:
        ts_code += '.' + order.m_strExchangeID

    try:
        cancel_task(task_id, g.accID, 'STOCK', ContextInfo)
        print("[qtrade_bridge] [{0}] 撤单已发送: {1} task={2}".format(
            sig_id, ts_code, task_id))
        return True
    except Exception as e:
        print("[qtrade_bridge] [{0}] cancel_task失败: {1}".format(sig_id, e))
        return False


def _update_status(sig_id, status, broker_order_id=None, error_msg=None):
    body = {"status": status}
    if broker_order_id:
        body["broker_order_id"] = broker_order_id
    if error_msg:
        body["error_msg"] = error_msg

    ok = _http_put(MAC_HOST + "/api/trade/{0}/status".format(sig_id), body)
    if ok:
        print("[qtrade_bridge] [{0}] -> {1}".format(sig_id, status))
    else:
        print("[qtrade_bridge] [{0}] status update failed".format(sig_id))
