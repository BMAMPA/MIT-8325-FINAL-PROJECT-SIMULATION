import threading
from datetime import datetime, timedelta, timezone

import jwt

import app as application

ENDPOINT = "/api/v1/smart-meter/load-credit"


def setup_function():
    with application.LOCK:
        application.accounts.clear()
        application.accounts.update({
            item["cid"]: dict(item)
            for item in application.DEFAULT_ACCOUNTS
        })
        application.processed_transactions.clear()
        application.ledger.clear()


def client():
    application.app.config["TESTING"] = True
    return application.app.test_client()


def auth(test_client):
    token = test_client.get("/api/v1/auth/demo-token").json["token"]
    return {"Authorization": f"Bearer {token}"}


def post_credit(test_client, headers=None, **overrides):
    data = {
        "transaction_id": "TEST-TXN-001",
        "customer_id": "IB001245",
        "meter_id": "SM4509876",
        "amount": 500,
        "payment_status": "CONFIRMED",
    }
    data.update(overrides)
    return test_client.post(ENDPOINT, json=data, headers=headers or auth(test_client))


def test_home_page_loads():
    assert client().get("/").status_code == 200


def test_valid_credit_adds_balance():
    response = post_credit(client())
    assert response.status_code == 200
    assert response.json["balance"] == 1700.0


def test_short_alias_route_still_works():
    c = client()
    response = c.post("/api/credit", json={"transaction_id": "ALIAS-1", "customer_id": "IB001245",
                                           "meter_id": "SM4509876", "amount": 100,
                                           "payment_status": "CONFIRMED"}, headers=auth(c))
    assert response.status_code == 200


def test_duplicate_transaction_is_rejected():
    test_client = client()
    post_credit(test_client)
    response = post_credit(test_client)
    assert response.status_code == 409
    assert application.accounts["IB001245"]["balance"] == 1700.0   # credited once only


def test_bad_token_is_rejected():
    response = post_credit(client(), headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401


def test_missing_token_is_rejected():
    response = client().post(ENDPOINT, json={"transaction_id": "T1"})
    assert response.status_code == 401


def test_expired_token_is_rejected():
    expired = jwt.encode({"sub": "psp", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                         application.app.config["JWT_SECRET"], algorithm="HS256")
    response = post_credit(client(), headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert "TOKEN_EXPIRED" in response.json["status"]


def test_unconfirmed_payment_is_rejected():
    response = post_credit(client(), payment_status="PENDING")
    assert response.status_code == 400
    assert application.accounts["IB001245"]["balance"] == 1200.0


def test_invalid_amount_is_rejected():
    for bad in (0, -50, "abc", 5_000_000):
        assert post_credit(client(), transaction_id=f"AMT-{bad}", amount=bad).status_code == 400


def test_unknown_customer_is_rejected():
    response = post_credit(client(), customer_id="IB999999")
    assert response.status_code == 404


def test_wrong_meter_is_rejected():
    response = post_credit(client(), meter_id="SM7788990")
    assert response.status_code == 403
    assert response.json["balance"] is None   # no other customer's balance is disclosed


def test_audit_chain_verifies():
    test_client = client()
    post_credit(test_client)
    post_credit(test_client)   # rejected replay is audited too
    response = test_client.get("/api/audit/verify")
    assert response.status_code == 200
    assert response.json["valid"] is True
    assert response.json["records_checked"] == 2


def test_tampered_audit_record_is_detected():
    test_client = client()
    for i in range(4):
        post_credit(test_client, transaction_id=f"CHAIN-{i}")
    application.ledger[1]["amount"] = 999999.0          # an insider edits record 2
    response = test_client.get("/api/audit/verify")
    assert response.status_code == 409
    assert response.json["record"] == 2


def test_concurrent_replays_credit_only_once():
    test_client = client()
    headers = auth(test_client)
    codes = []

    def fire():
        c = application.app.test_client()
        codes.append(post_credit(c, headers=headers, transaction_id="RACE-1").status_code)

    threads = [threading.Thread(target=fire) for _ in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert codes.count(200) == 1 and codes.count(409) == 19
    assert application.accounts["IB001245"]["balance"] == 1700.0
