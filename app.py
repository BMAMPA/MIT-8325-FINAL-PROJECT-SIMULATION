import time
import jwt
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify

app = Flask(__name__)
app.config["SECRET_KEY"] = "SDSMCLF_SECRET_KEY_2026"

# Simulated Persistence Layer (Customers, Registered Meters & Processed Transactions)
customers = {
    "IB001245": {"name": "Musa Adewale", "meter_id": "SM4509876", "active": True}
}

meters = {
    "SM4509876": {"customer_id": "IB001245", "balance": 1200.00, "status": "ACTIVE"}
}

processed_transactions = set()
audit_logs = []


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def require_jwt(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == "OPTIONS":
            return jsonify({"status": "OK"}), 200
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"status": "FAILED", "errorCode": "MISSING_TOKEN", "message": "Authorization token required"}), 401
        parts = auth_header.split(" ")
        if len(parts) != 2:
            return jsonify({"status": "FAILED", "errorCode": "MALFORMED_TOKEN", "message": "Malformed Bearer header"}), 401
        token = parts[1]
        try:
            jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"status": "FAILED", "errorCode": "TOKEN_EXPIRED", "message": "JWT token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "FAILED", "errorCode": "INVALID_TOKEN", "message": "Invalid authentication token"}), 401
        return f(*args, **kwargs)
    return decorated


def log_audit(txn_id, meter_id, amount, outcome, latency_ms):
    audit_logs.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transactionID": txn_id,
        "meterID": meter_id,
        "amount": amount,
        "status": outcome,
        "latencyMs": round(latency_ms, 2)
    })


@app.route("/api/v1/auth/demo-token", methods=["GET", "OPTIONS"])
def issue_demo_token():
    if request.method == "OPTIONS":
        return jsonify({"status": "OK"}), 200
    payload = {
        "user": "payment_gateway_psp",
        "role": "AUTHORIZED_VENDING_PARTNER",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30)
    }
    token = jwt.encode(payload, app.config["SECRET_KEY"], algorithm="HS256")
    return jsonify({"token": token}), 200


@app.route("/api/v1/smart-meter/load-credit", methods=["POST", "OPTIONS"])
@require_jwt
def load_credit():
    start_time = time.perf_counter()
    data = request.get_json(silent=True) or {}

    txn_id = data.get("transactionID")
    customer_id = data.get("customerID")
    meter_id = data.get("meterID")
    amount = data.get("amount", 0)
    payment_status = data.get("paymentStatus")

    # 1. Input & Payment Verification
    if not all([txn_id, customer_id, meter_id]) or float(amount) <= 0:
        latency = (time.perf_counter() - start_time) * 1000
        log_audit(txn_id, meter_id, amount, "INVALID_PAYLOAD", latency)
        return jsonify({"status": "FAILED", "errorCode": "INVALID_REQUEST", "message": "Missing or invalid transaction parameters"}), 400

    if payment_status != "CONFIRMED":
        latency = (time.perf_counter() - start_time) * 1000
        log_audit(txn_id, meter_id, amount, "PAYMENT_UNCONFIRMED", latency)
        return jsonify({"status": "FAILED", "errorCode": "PAYMENT_NOT_VERIFIED", "message": "Payment gateway confirmation required"}), 400

    # 2. Idempotency Check (Replay Protection)
    if txn_id in processed_transactions:
        latency = (time.perf_counter() - start_time) * 1000
        log_audit(txn_id, meter_id, amount, "DUPLICATE_BLOCKED", latency)
        return jsonify({"status": "FAILED", "errorCode": "DUPLICATE_TRANSACTION", "message": "Transaction ID already processed"}), 409

    # 3. Customer & Smart Meter Ownership Validation
    customer = customers.get(customer_id)
    meter = meters.get(meter_id)

    if not customer or not customer["active"] or not meter or meter["status"] != "ACTIVE":
        latency = (time.perf_counter() - start_time) * 1000
        log_audit(txn_id, meter_id, amount, "METER_NOT_FOUND", latency)
        return jsonify({"status": "FAILED", "errorCode": "METER_VALIDATION_FAILED", "message": "Active customer or smart meter not found"}), 404

    if meter["customer_id"] != customer_id:
        latency = (time.perf_counter() - start_time) * 1000
        log_audit(txn_id, meter_id, amount, "OWNERSHIP_MISMATCH", latency)
        return jsonify({"status": "FAILED", "errorCode": "OWNERSHIP_MISMATCH", "message": "Meter does not belong to customer"}), 403

    # 4. Direct Credit Activation & Immutable Audit Logging
    meters[meter_id]["balance"] += float(amount)
    processed_transactions.add(txn_id)
    latency = (time.perf_counter() - start_time) * 1000
    log_audit(txn_id, meter_id, amount, "SUCCESS", latency)

    return jsonify({
        "status": "SUCCESS",
        "message": "Electricity credit loaded successfully",
        "transactionID": txn_id,
        "meterID": meter_id,
        "newBalance": meters[meter_id]["balance"],
        "processingLatencyMs": round(latency, 2),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
