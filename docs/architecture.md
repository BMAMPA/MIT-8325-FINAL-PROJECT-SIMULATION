# Architecture

## Implemented simulation (MIT-8325-FINAL-PROJECT-SIMULATION)

Browser dashboard (templates/index.html, static/app.js)
    -> GET  /api/v1/auth/demo-token          short-lived HS256 JWT for the payment partner
    -> POST /api/v1/smart-meter/load-credit  (alias: /api/credit)
         1. JWT signature + expiry check          -> 401
         2. transaction ID, IDs, amount           -> 400
         3. payment_status == CONFIRMED           -> 400
         --- under one lock ---
         4. replay lock on transaction_id         -> 409
         5. customer exists                       -> 404
         6. meter belongs to customer             -> 403
         7. credit balance                        -> 200
         8. append SHA-256 hash-chained audit record (every outcome)
    -> GET  /api/audit, /api/audit/verify    view and verify the audit chain

Data is in memory and resets on restart.

## Proposed production architecture (not implemented)

Payment service provider -> API gateway (mTLS, OAuth 2.0 with RS256/ES256 JWT,
rate limiting) -> transaction service (idempotency via PRIMARY KEY in PostgreSQL,
billing/tariff conversion, message queue) -> meter integration service
(IEC 62056 DLMS/COSEM, STS token fallback when a meter is offline) ->
access-controlled, write-once audit storage.
