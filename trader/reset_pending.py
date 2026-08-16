"""
reset_pending.py — 通过 API 将指定状态的信号改为 pending

用法:
  python3 reset_pending.py              # 把 sent 状态的改为 pending
  python3 reset_pending.py --all        # 把所有信号改为 pending
"""

import sys
import time
import requests

API_KEY = "8bb68f1cefeaeecd06aa748250eca031"
BASE = "https://594marc.cc"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
ADMIN_PASS = "Hpxi9769!"


def get_all_signals(limit=500):
    login_resp = requests.post(
        f"{BASE}/api/admin/login",
        json={"username": "admin", "password": ADMIN_PASS},
        timeout=10,
    )
    if login_resp.status_code != 200:
        print(f"[ERROR] login failed: {login_resp.status_code} {login_resp.text[:100]}")
        return []

    jwt = login_resp.json()["access_token"]
    jwt_headers = {"Authorization": f"Bearer {jwt}"}
    url = f"{BASE}/api/admin/trades?limit={limit}"
    resp = requests.get(url, headers=jwt_headers, timeout=10)
    if resp.status_code != 200:
        print(f"[ERROR] fetch trades failed: {resp.status_code}")
        return []

    return resp.json()


def set_signal_pending(signal_id):
    """通过 trade API 将信号状态改为 pending（用 update status 端点）"""
    resp = requests.put(
        f"{BASE}/api/trade/{signal_id}/status",
        json={"status": "pending", "broker_order_id": None},
        headers=HEADERS,
        timeout=10,
    )
    return resp.status_code == 200


def main():
    reset_all = "--all" in sys.argv

    print("Fetching signals...")
    signals = get_all_signals()

    if not signals:
        print("No signals found.")
        return

    targets = []
    for s in signals:
        if reset_all or s["status"] == "sent":
            targets.append(s)

    if not targets:
        print(f"No signals to reset (found {len(signals)}, all already pending).")
        return

    print(f"Resetting {len(targets)} signal(s) to pending:")
    for s in targets:
        ok = set_signal_pending(s["id"])
        status = "OK" if ok else "FAIL"
        print(f"  [{s['id']}] {s['symbol']} ({s['status']} → pending) {status}")
        time.sleep(0.5)  # 避免并发写入冲突

    print("Done.")


if __name__ == "__main__":
    main()
