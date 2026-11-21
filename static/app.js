"use strict";

const $ = id => document.getElementById(id);
let accounts = [];
let selectedAccount = null;
let lastTransaction = null;
let token = null;
let auditCache = [];

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  return { response, data };
}

function log(message, clear = false) {
  const box = $("console");
  if (clear) box.textContent = "";
  box.textContent += `${box.textContent ? "\n" : ""}> ${message}`;
  box.scrollTop = box.scrollHeight;
}

async function getToken() {
  const { data } = await api("/api/v1/auth/demo-token");
  token = data.token;
  log(`Gateway issued a signed HS256 token (valid ${data.expires_in_minutes} min).`);
}

async function loadAccounts() {
  const { data } = await api("/api/accounts");
  accounts = data;

  const select = $("accountSelect");
  accounts.forEach((account, index) => {
    const option = document.createElement("option");
    option.value = index;
    option.textContent = `${account.name} — ${account.cid} | ${account.mid}`;
    select.append(option);
  });

  select.addEventListener("change", showAccount);
  showAccount();
}

function showAccount() {
  selectedAccount = accounts[Number($("accountSelect").value)];
  if (!selectedAccount) return;

  $("custName").value = selectedAccount.name;
  $("custId").value = selectedAccount.cid;
  $("meterId").value = selectedAccount.mid;
  renderMeter(selectedAccount.balance);
}

function renderMeter(balance = selectedAccount?.balance || 0) {
  const amount = Number($("amount").value || 0);
  const tariff = 68;

  $("previewBox").textContent =
    `Preview: ₦${amount.toLocaleString("en-NG")} ≈ +${(amount / tariff).toFixed(3)} kWh`;

  $("lcdId").textContent = `ID: ${$("meterId").value}`;
  $("lcdTariff").textContent = `TARIFF: ₦${tariff}/kWh`;
  $("lcdNgn").textContent = `BAL: ₦${Number(balance).toLocaleString("en-NG", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`;
  $("lcdKwh").textContent = `${(Number(balance) / tariff).toFixed(2)} kWh`;
  $("subDisplay").textContent = `${selectedAccount.name} (${selectedAccount.cid})`;
}

async function sendTransaction(mode) {
  if (!selectedAccount) return;

  let transactionId = $("txnId").value.trim();
  let meterId = $("meterId").value.trim();

  if (mode === "REPLAY") {
    transactionId = lastTransaction || transactionId;
  }

  if (mode === "MISMATCH") {
    meterId = accounts.find(a => a.cid !== selectedAccount.cid)?.mid || "SM0000000";
  }

  const bearer = mode === "BAD_JWT" ? "forged.invalid.token" : token;
  const payload = {
    transaction_id: transactionId,
    customer_id: $("custId").value.trim(),
    meter_id: meterId,
    amount: Number($("amount").value),
    payment_status: mode === "UNPAID" ? "PENDING" : "CONFIRMED"
  };

  try {
    const { response, data } = await api("/api/v1/smart-meter/load-credit", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${bearer}`
      },
      body: JSON.stringify(payload)
    });

    log(`${data.status}: ${data.transaction_id || "(empty transaction ID)"} — server processing ${data.processing_latency_ms} ms`, true);
    $("lcdBanner").textContent = response.ok ? "CREDIT APPLIED — NO TOKEN ENTRY" : `ALERT ${data.status} — BALANCE UNCHANGED`;
    document.querySelector(".lcd").classList.toggle("alert", !response.ok);
    if (!response.ok) log("Request rejected. No credit issued; the attempt is recorded in the audit chain.");

    if (response.ok) {
      lastTransaction = transactionId;
      selectedAccount.balance = data.balance;
      $("txnId").value = `TXN${Date.now()}`;
      renderMeter(data.balance);
    }

    await refreshAudit();
  } catch (error) {
    log(`Request failed: ${error.message}`, true);
  }
}

async function refreshAudit() {
  const { data } = await api("/api/audit");
  auditCache = data.events;
  const body = $("auditRows");
  body.replaceChildren();

  data.events.forEach(event => {
    const row = document.createElement("tr");
    const values = [
      new Date(event.time).toLocaleTimeString(),
      event.transaction_id,
      event.meter_id,
      `₦${Number(event.amount).toLocaleString("en-NG")}`,
      event.status
    ];

    values.forEach((value, index) => {
      const cell = document.createElement("td");
      if (index === values.length - 1) {
        const badge = document.createElement("span");
        badge.className = String(value).startsWith("200") ? "ok" : "bad";
        badge.textContent = value;
        cell.append(badge);
      } else {
        cell.textContent = value;
      }
      row.append(cell);
    });
    body.append(row);
  });

  $("hashBar").textContent = data.chain_head
    ? `SHA-256 chain head: ${data.chain_head} (${data.count} records)`
    : "SHA-256 chain head: empty";
}

async function verifyAudit() {
  const { response, data } = await api("/api/audit/verify");
  $("benchBox").style.display = "block";
  $("benchBox").textContent = response.ok
    ? `✓ CHAIN VERIFIED\nRecords checked: ${data.records_checked}\n${data.chain_head}`
    : `⚠ VERIFICATION FAILED at record ${data.record}`;
}

function exportCsv() {
  if (!auditCache.length) { log("No audit records yet — run a transaction first."); return; }
  const head = "time,transaction_id,meter_id,amount,status,latency_ms,previous_hash,hash";
  const rows = auditCache.slice().reverse().map(e =>
    [e.time, e.transaction_id, e.meter_id, e.amount, `"${e.status}"`, e.latency_ms, e.previous_hash, e.hash].join(","));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([head + "\n" + rows.join("\n")], { type: "text/csv" }));
  link.download = "sdsmclf_audit_chain.csv";
  document.body.append(link); link.click(); link.remove();
  log(`Exported ${auditCache.length} audit records with their hash links.`);
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await getToken();
    await loadAccounts();
    await refreshAudit();

    $("amount").addEventListener("input", () => renderMeter());
    $("btnSendValid").addEventListener("click", () => sendTransaction("VALID"));
    $("btnSendReplay").addEventListener("click", () => sendTransaction("REPLAY"));
    $("btnSendBadJwt").addEventListener("click", () => sendTransaction("BAD_JWT"));
    $("btnMismatch").addEventListener("click", () => sendTransaction("MISMATCH"));
    $("btnUnpaid").addEventListener("click", () => sendTransaction("UNPAID"));
    $("btnUnknown").addEventListener("click", async () => {
      // Same request path as a valid top-up, but naming a customer ID that is not registered.
      const keep = $("custId").value;
      $("custId").value = "IB999999";
      try { await sendTransaction("VALID"); } finally { $("custId").value = keep; }
    });
    $("btnCsv").addEventListener("click", exportCsv);
    $("btnRefreshAudit").addEventListener("click", refreshAudit);
    $("btnVerify").addEventListener("click", verifyAudit);

    log("Ready. Token obtained; every button calls the live Flask API. Local simulation only.");
  } catch (error) {
    log(`Could not load application: ${error.message}`, true);
  }
});