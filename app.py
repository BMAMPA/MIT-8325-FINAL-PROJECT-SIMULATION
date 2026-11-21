"""
SDSMCLF v1.0 — Secure Direct Smart Meter Credit Loading Framework
MIT-8325-FINAL-PROJECT-SIMULATION: educational Flask prototype (simulation only).

Validation pipeline for every credit request, in this order:
    1. HS256 JWT bearer token                        -> 401 if missing, expired or forged
    2. Payload: transaction ID, IDs, amount           -> 400 if malformed
    3. Payment confirmed by the gateway               -> 400 if not CONFIRMED
    4. Replay lock on transaction_id (idempotency)    -> 409 if already processed
    5. Customer exists                                -> 404 if unknown
    6. Meter belongs to that customer                 -> 403 if ownership mismatch
    7. Credit the balance + append a hash-chained audit record -> 200
Steps 4–7 run under one lock, so two simultaneous copies of a transaction
can never both be credited. Every outcome, accepted or rejected, is audited.
"""
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import jwt
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
# Demo signing key. A production deployment would use asymmetric keys
# (RS256/ES256) held in an HSM, with mutual TLS between services.
app.config["JWT_SECRET"] = "sdsmclf-demo-signing-key-change-me-2026!"
JWT_ALGORITHM = "HS256"
TOKEN_MINUTES = 30

LOCK = threading.Lock()
ZERO_HASH = "0" * 64

DEFAULT_ACCOUNTS = [
    {"cid": "IB001245", "name": "Musa Adewale", "mid": "SM4509876", "balance": 1200.0},
    {"cid": "IB002390", "name": "Amina Bello", "mid": "SM7788990", "balance": 3400.0},
    {"cid": "IB004112", "name": "Chinedu Okafor", "mid": "SM1122334", "balance": 500.0},
]

accounts = {a["cid"]: dict(a) for a in DEFAULT_ACCOUNTS}
processed_transactions = set()
ledger = []


def valid_identifier(value):
    return isinstance(value, str) and bool(
        re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
    )


def issue_token(partner="payment_gateway_psp", minutes=TOKEN_MINUTES):
    payload = {
        "sub": partner,
        "role": "AUTHORIZED_VENDING_PARTNER",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, app.config["JWT_SECRET"], algorithm=JWT_ALGORITHM)


def token_error():
    """Return None if the bearer token is valid, else a short reason."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return "MISSING_TOKEN"
    try:
        jwt.decode(header[7:], app.config["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return "TOKEN_EXPIRED"
    except jwt.InvalidTokenError:
        return "INVALID_TOKEN"
    return None


def hash_event(previous_hash, event):
    payload = json.dumps(
        {"previous": previous_hash, **event},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_event(txn_id, meter_id, amount, status, latency_ms):
    previous = ledger[-1]["hash"] if ledger else ZERO_HASH
    event = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "transaction_id": txn_id,
        "meter_id": meter_id,
        "amount": float(amount),
        "status": status,
        "latency_ms": round(latency_ms, 3),
    }
    # The hash covers the event fields and the previous record's hash, so
    # changing or deleting any earlier record breaks every later link.
    event["hash"] = hash_event(previous, event)
    event["previous_hash"] = previous
    ledger.append(event)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/v1/auth/demo-token")
def demo_token():
    # Stands in for the payment partner's credential exchange (OAuth 2.0).
    return jsonify({"token": issue_token(), "expires_in_minutes": TOKEN_MINUTES})


@app.get("/api/accounts")
def get_accounts():
    with LOCK:
        return jsonify(list(accounts.values()))


@app.get("/api/audit")
def get_audit():
    with LOCK:
        return jsonify({
            "events": list(reversed(ledger[-50:])),
            "count": len(ledger),
            "chain_head": ledger[-1]["hash"] if ledger else None,
        })


@app.get("/api/audit/verify")
def verify_audit():
    with LOCK:
        previous = ZERO_HASH

        for index, stored in enumerate(ledger):
            event = {
                key: stored[key]
                for key in (
                    "time", "transaction_id", "meter_id",
                    "amount", "status", "latency_ms"
                )
            }
            expected = hash_event(previous, event)

            if stored["previous_hash"] != previous or stored["hash"] != expected:
                return jsonify({
                    "valid": False,
                    "record": index + 1,
                    "message": "Audit chain verification failed",
                }), 409

            previous = stored["hash"]

        return jsonify({
            "valid": True,
            "records_checked": len(ledger),
            "chain_head": previous,
        })


@app.post("/api/v1/smart-meter/load-credit")
@app.post("/api/credit")  # short alias kept for backward compatibility
def credit():
    started = time.perf_counter()
    data = request.get_json(silent=True) or {}

    txn_id = data.get("transaction_id", "")
    customer_id = data.get("customer_id", "")
    meter_id = data.get("meter_id", "")
    payment_status = data.get("payment_status", "")

    try:
        amount = Decimal(str(data.get("amount", "")))
    except (InvalidOperation, ValueError):
        amount = Decimal("-1")
    if not amount.is_finite():
        amount = Decimal("-1")

    reason = token_error()
    if reason:
        status, code = f"401 {reason}", 401
    elif not valid_identifier(txn_id):
        status, code = "400 INVALID_TRANSACTION_ID", 400
    elif not valid_identifier(customer_id) or not valid_identifier(meter_id):
        status, code = "400 INVALID_IDENTIFIER", 400
    elif amount <= 0 or amount > Decimal("1000000"):
        status, code = "400 INVALID_AMOUNT", 400
    elif payment_status != "CONFIRMED":
        status, code = "400 PAYMENT_NOT_CONFIRMED", 400
    else:
        status, code = "200 SUCCESS", 200

    with LOCK:
        account = accounts.get(customer_id)

        if code == 200 and txn_id in processed_transactions:
            status, code = "409 DUPLICATE_TRANSACTION", 409
        elif code == 200 and account is None:
            status, code = "404 CUSTOMER_NOT_FOUND", 404
        elif code == 200 and account["mid"] != meter_id:
            status, code = "403 OWNERSHIP_MISMATCH", 403

        if code == 200:
            account["balance"] = round(account["balance"] + float(amount), 2)
            processed_transactions.add(txn_id)

        latency_ms = (time.perf_counter() - started) * 1000
        record_event(
            txn_id or "(empty)",
            meter_id or "(empty)",
            amount if amount > 0 else 0,
            status,
            latency_ms,
        )

        return jsonify({
            "status": status,
            "transaction_id": txn_id,
            "meter_id": meter_id,
            "balance": account["balance"] if (account and code == 200) else None,
            "processing_latency_ms": round(latency_ms, 3),
            "simulated": True,
        }), code


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "Request body exceeds demo size limit"}), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
