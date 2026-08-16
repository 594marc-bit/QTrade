"""
TraderTest.py — 模拟券商端脚本
轮询云端 /api/trade/pending，获取待执行信号。

用法:
  python TraderTest.py                       # 持续轮询（只读），Ctrl+C 退出
  python TraderTest.py 3                     # 只轮询 3 次（只读）
  python TraderTest.py --process sent 1      # 轮询 1 次，将 pending 信号改为 sent
                                              # 状态可选: pending / sent / cancelled
"""

import sys
import time
import requests

API_KEY = "8bb68f1cefeaeecd06aa748250eca031"
BASE = "https://594marc.cc"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def fetch_pending():
    resp = requests.get(f"{BASE}/api/trade/pending", headers=HEADERS, timeout=10)
    if resp.status_code != 200:
        print(f"[ERROR] fetch pending failed: {resp.status_code} {resp.text[:200]}")
        return []
    return resp.json()


def update_status(signal_id, status, broker_order_id=None, error_msg=None):
    body = {"status": status}
    if broker_order_id:
        body["broker_order_id"] = broker_order_id
    if error_msg:
        body["error_msg"] = error_msg
    resp = requests.put(
        f"{BASE}/api/trade/{signal_id}/status",
        json=body,
        headers=HEADERS,
        timeout=10,
    )
    return resp.status_code == 200


def main():
    # 解析参数
    args = sys.argv[1:]
    process = False
    target_status = "sent"
    max_loops = 0

    i = 0
    while i < len(args):
        if args[i] == "--process":
            process = True
            if i + 1 < len(args):
                target_status = args[i + 1]
                i += 1
        else:
            try:
                max_loops = int(args[i])
            except ValueError:
                print(f"Usage: python TraderTest.py [--process pending|sent|cancelled] [loops]")
                sys.exit(1)
        i += 1

    action_label = f"→ {target_status}" if process else "→ (read-only)"
    if max_loops:
        print(f"TraderTest — {action_label}, {max_loops} loop(s), every 5s\n")
    else:
        print(f"TraderTest — {action_label}, continuous, Ctrl+C to stop\n")

    seq = 0
    loop_count = 0
    while max_loops == 0 or loop_count < max_loops:
        try:
            signals = fetch_pending()
            if signals:
                for s in signals:
                    print(f"[{s['id']}] {s['symbol']} {s['action'].upper()} "
                          f"{s['quantity']}股 @ {s['price'] if s['price'] > 0 else 'MKT'} "
                          f"→ {target_status}", end="")
                    if process:
                        ok = update_status(s['id'], target_status)
                        print(" OK" if ok else " FAIL")
                    else:
                        print()
            else:
                seq += 1
                if seq % 12 == 0:
                    print(f"[ping] no pending signals")
        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(5)
        loop_count += 1

    print("Done.")


if __name__ == "__main__":
    main()
