"""
benchmark_sdsmclf.py — N = 100 functional, security and latency benchmark for SDSMCLF v1.0 (MIT8212-Simu)

Start the API first (python app.py), then run:  python benchmark_sdsmclf.py
Results are written to results/benchmark_results.csv and results/summary.json.

Workload (fixed seed, attacks interleaved among valid requests):
  TC-01  70  valid top-ups across the three demo accounts       -> 200
  TC-02   6  replay of an already-processed transaction_id      -> 409
  TC-03   6  missing (2), expired (2) or forged (2) JWT          -> 401
  TC-04   6  payment not confirmed (PENDING / FAILED)            -> 400
  TC-05   6  unknown customer ID                                 -> 404
  TC-06   6  meter belonging to another customer                 -> 403
Additional checks:
  * balance integrity: every account ends at seed + its valid top-ups (no credit leaked)
  * concurrency: 20 simultaneous submissions of ONE new transaction_id -> exactly 1 credited
  * audit chain: /api/audit/verify must report a valid chain covering every request
"""
import csv, json, os, random, statistics, sys, threading, time
from datetime import datetime, timedelta, timezone

import jwt
import requests

BASE = os.environ.get("SDSMCLF_URL", "http://127.0.0.1:5000")
ENDPOINT = f"{BASE}/api/v1/smart-meter/load-credit"
SECRET = "sdsmclf-demo-signing-key-change-me-2026!"   # used only to mint an EXPIRED token
random.seed(8325)


def pct(data, p):
    s = sorted(data); k = (len(s) - 1) * p / 100; f = int(k); c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def main():
    try:
        token = requests.get(f"{BASE}/api/v1/auth/demo-token", timeout=5).json()["token"]
        accounts = requests.get(f"{BASE}/api/accounts", timeout=5).json()
    except Exception as e:
        sys.exit(f"Cannot reach the API at {BASE}. Start it first with:  python app.py\n({e})")
    seed = {a["cid"]: a["balance"] for a in accounts}
    ok = {"Authorization": f"Bearer {token}"}
    expired = jwt.encode({"sub": "psp", "exp": datetime.now(timezone.utc) - timedelta(minutes=5)}, SECRET, algorithm="HS256")
    forged = jwt.encode({"sub": "attacker"}, "not-the-real-key-but-long-enough-32b", algorithm="HS256")

    def body(txn, acct, amount=5000, meter=None, cid=None, pay="CONFIRMED"):
        return {"transaction_id": txn, "customer_id": cid or acct["cid"], "meter_id": meter or acct["mid"],
                "amount": amount, "payment_status": pay}

    plan = []
    valid_by = {a["cid"]: 0.0 for a in accounts}
    for i in range(70):
        acct = accounts[i % 3]
        amount = 5000 if i == 0 else random.choice([1000, 2000, 2500, 5000, 10000])
        txn = "TXN900123" if i == 0 else f"TXN{910000 + i}"
        plan.append(("TC-01 Valid credit load", 200, ok, body(txn, acct, amount)))
        valid_by[acct["cid"]] += amount
    attacks = []
    for i in range(6):
        attacks.append(("TC-02 Duplicate replay", 409, ok, body("TXN900123", accounts[0])))
    for i, h in enumerate([{}, {}, {"Authorization": f"Bearer {expired}"}, {"Authorization": f"Bearer {expired}"},
                           {"Authorization": f"Bearer {forged}"}, {"Authorization": f"Bearer {forged}"}]):
        attacks.append(("TC-03 Unauthenticated call", 401, h, body(f"TXN930{i}", accounts[0])))
    for i in range(6):
        attacks.append(("TC-04 Unconfirmed payment", 400, ok, body(f"TXN940{i}", accounts[i % 3], pay=random.choice(["PENDING", "FAILED"]))))
    for i in range(6):
        attacks.append(("TC-05 Unknown customer", 404, ok, body(f"TXN950{i}", accounts[0], cid="IB999999")))
    for i in range(6):
        victim = accounts[(i + 1) % 3]
        attacks.append(("TC-06 Ownership mismatch", 403, ok, body(f"TXN960{i}", accounts[i % 3], meter=victim["mid"])))
    random.shuffle(attacks)
    rest, merged = plan[1:], [plan[0]]
    slots = set(random.sample(range(len(rest) + len(attacks)), len(attacks)))
    a = v = 0
    for pos in range(len(rest) + len(attacks)):
        if pos in slots: merged.append(attacks[a]); a += 1
        else: merged.append(rest[v]); v += 1

    s = requests.Session(); rows, rtt, server = [], [], []
    t0 = time.perf_counter()
    for n, (cat, exp, h, b) in enumerate(merged, 1):
        st = time.perf_counter(); r = s.post(ENDPOINT, json=b, headers=h, timeout=10)
        el = (time.perf_counter() - st) * 1000; d = r.json()
        rtt.append(el); server.append(d.get("processing_latency_ms", 0.0))
        rows.append({"n": n, "category": cat, "transaction_id": b["transaction_id"], "expected": exp,
                     "observed": r.status_code, "passed": r.status_code == exp, "status": d.get("status", ""),
                     "server_ms": d.get("processing_latency_ms"), "round_trip_ms": round(el, 3)})
    wall = time.perf_counter() - t0

    final = {a["cid"]: a["balance"] for a in requests.get(f"{BASE}/api/accounts").json()}
    balance_ok = all(abs(final[c] - (seed[c] + valid_by[c])) < 0.01 for c in seed)

    # Concurrency: 20 threads submit the same brand-new transaction at once.
    race_codes = []; barrier = threading.Barrier(20)
    def fire():
        barrier.wait()
        race_codes.append(requests.post(ENDPOINT, json=body("RACE-TXN-0001", accounts[1], 1000), headers=ok, timeout=10).status_code)
    ths = [threading.Thread(target=fire) for _ in range(20)]
    [t.start() for t in ths]; [t.join() for t in ths]
    after = requests.get(f"{BASE}/api/accounts").json()
    race_ok = race_codes.count(200) == 1 and race_codes.count(409) == 19 and \
        abs(next(x["balance"] for x in after if x["cid"] == accounts[1]["cid"]) - (final[accounts[1]["cid"]] + 1000)) < 0.01

    verify = requests.get(f"{BASE}/api/audit/verify").json()

    cats = {}
    for r in rows:
        c = cats.setdefault(r["category"], {"n": 0, "passed": 0, "expected": r["expected"]}); c["n"] += 1; c["passed"] += r["passed"]
    passed = sum(r["passed"] for r in rows)
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0], "platform": sys.platform,
        "requests_total": len(rows), "requests_passed": passed, "pass_rate_pct": round(100 * passed / len(rows), 1),
        "categories": dict(sorted(cats.items())), "balance_integrity": balance_ok,
        "server_processing_ms": {"mean": round(statistics.mean(server), 3), "p95": round(pct(server, 95), 3), "max": round(max(server), 3)},
        "round_trip_ms": {"mean": round(statistics.mean(rtt), 2), "p95": round(pct(rtt, 95), 2), "max": round(max(rtt), 2)},
        "throughput_req_per_s": round(len(rows) / wall, 1),
        "concurrency": {"submissions": 20, "credited": race_codes.count(200), "rejected_409": race_codes.count(409), "passed": race_ok},
        "audit_chain": verify,
    }
    os.makedirs("results", exist_ok=True)
    with open("results/benchmark_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    with open("results/summary.json", "w") as f: json.dump(summary, f, indent=2)

    print("\nSDSMCLF v1.0 — N = 100 functional & security benchmark")
    print("-" * 64)
    for k, c in summary["categories"].items(): print(f"{k:<30} expect {c['expected']}   {c['passed']:>2} / {c['n']}")
    print("-" * 64)
    print(f"Pass rate          : {passed} / {len(rows)} ({summary['pass_rate_pct']}%)")
    print(f"Balance integrity  : {'OK' if balance_ok else 'FAILED'} (all three accounts)")
    sp, rt = summary["server_processing_ms"], summary["round_trip_ms"]
    print(f"Server processing  : mean {sp['mean']} ms | p95 {sp['p95']} ms | max {sp['max']} ms")
    print(f"HTTP round trip    : mean {rt['mean']} ms | p95 {rt['p95']} ms | max {rt['max']} ms")
    print(f"Throughput         : {summary['throughput_req_per_s']} requests/s (sequential, one client)")
    print(f"Concurrency        : 20 simultaneous copies -> {race_codes.count(200)} credited, {race_codes.count(409)} rejected (409) {'OK' if race_ok else 'FAILED'}")
    print(f"Audit chain        : {'VALID' if verify.get('valid') else 'BROKEN'} — {verify.get('records_checked')} records checked")
    print("Results written to results/benchmark_results.csv and results/summary.json\n")
    sys.exit(0 if passed == len(rows) and balance_ok and race_ok and verify.get("valid") else 1)


if __name__ == "__main__":
    main()
