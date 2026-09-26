// In local dev, Vite's dev-only proxy (vite.config.js) forwards "/api" to
// the backend, so the default works with no env var set. In production
// (Vercel serves only the static build — no proxy exists there), this must
// point at the deployed backend's public URL, e.g.
// "https://vriddhi-backend.onrender.com/api".
const BASE = import.meta.env.VITE_API_BASE_URL || "/api";

async function request(path, options) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error(body?.error || `Request failed: ${res.status}`);
    err.body = body;
    throw err;
  }
  return body;
}

export const api = {
  getMerchants: () => request("/merchants"),
  getMerchant: (id) => request(`/merchants/${id}`),
  getTransactions: (id, days) => request(`/merchants/${id}/transactions${days ? `?days=${days}` : ""}`),
  getCatalog: (id) => request(`/merchants/${id}/catalog`),
  getFeedback: (id) => request(`/merchants/${id}/feedback`),

  runScan: (merchantId) => request(`/agent/scan/${merchantId}`, { method: "POST" }),
  getLastScan: (merchantId) => request(`/agent/last-scan/${merchantId}`),
  dismissRecommendation: (merchantId, recId) =>
    request(`/recommendations/${merchantId}/${recId}/dismiss`, { method: "POST" }),
  endRecommendation: (merchantId, recId) =>
    request(`/actions/recommendation/${recId}/end`, { method: "POST", body: JSON.stringify({ merchantId }) }),

  sendCampaign: (payload) => request("/actions/campaign/send", { method: "POST", body: JSON.stringify(payload) }),
  sendReorder: (payload) => request("/actions/inventory/reorder", { method: "POST", body: JSON.stringify(payload) }),
  applyLoan: (payload) => request("/actions/lending/apply", { method: "POST", body: JSON.stringify(payload) }),
  applyInsurance: (payload) => request("/actions/insurance/apply", { method: "POST", body: JSON.stringify(payload) }),

  getAuditLog: (merchantId) => request(`/audit-log/${merchantId}`),

  sendChat: (merchantId, message) =>
    request(`/chat/${merchantId}`, { method: "POST", body: JSON.stringify({ message }) }),
  getChatHistory: (merchantId) => request(`/chat/${merchantId}/history`),
};
