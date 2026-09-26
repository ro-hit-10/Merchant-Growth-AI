import { useEffect, useState, useCallback } from "react";
import { api } from "./api";
import MerchantSwitcher from "./components/MerchantSwitcher";
import RevenueChart from "./components/RevenueChart";
import CatalogStatus from "./components/CatalogStatus";
import FeedbackList from "./components/FeedbackList";
import RecommendationCard from "./components/RecommendationCard";
import ChatPanel from "./components/ChatPanel";
import AuditLogModal from "./components/AuditLogModal";

export default function App() {
  const [merchants, setMerchants] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [merchant, setMerchant] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [feedback, setFeedback] = useState([]);

  const [recommendations, setRecommendations] = useState([]);
  const [results, setResults] = useState({});
  const [busyRecId, setBusyRecId] = useState(null);
  const [scanning, setScanning] = useState(false);

  const [auditEntries, setAuditEntries] = useState([]);
  const [showAudit, setShowAudit] = useState(false);

  const [view, setView] = useState("dashboard");
  const [chatMessages, setChatMessages] = useState([]);
  const [chatBusy, setChatBusy] = useState(false);

  useEffect(() => {
    api.getMerchants().then((list) => {
      setMerchants(list);
      if (list.length) setSelectedId(list[0].merchant_id);
    });
  }, []);

  const refreshAudit = useCallback((id) => {
    api.getAuditLog(id).then(setAuditEntries);
  }, []);

  const refreshCatalog = useCallback((id) => {
    api.getCatalog(id).then(setCatalog);
  }, []);

  const runScan = useCallback(async (id) => {
    setScanning(true);
    setResults({});
    try {
      const result = await api.runScan(id);
      setRecommendations(result.recommendations);
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;

    setRecommendations([]);
    setResults({});
    setChatMessages([]);
    setView("dashboard");

    Promise.all([
      api.getMerchant(selectedId),
      api.getTransactions(selectedId),
      api.getCatalog(selectedId),
      api.getFeedback(selectedId),
      api.getLastScan(selectedId),
      api.getChatHistory(selectedId),
    ]).then(([m, txns, cat, fb, lastScan, history]) => {
      if (cancelled) return;
      setMerchant(m);
      setTransactions(txns);
      setCatalog(cat);
      setFeedback(fb);
      refreshAudit(selectedId);
      setChatMessages(history.map((h) => ({ role: h.role, text: h.text })));

      if (lastScan) {
        setRecommendations(lastScan.recommendations);
      } else {
        runScan(selectedId);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [selectedId, runScan, refreshAudit]);

  const applyActionResult = (recId, res) => {
    setResults((prev) => ({
      ...prev,
      [recId]: { status: res.status, reason: res.auditEntry?.reason, guardrailChecks: res.guardrailChecks },
    }));
    refreshAudit(selectedId);
  };

  const handleApprove = async (rec) => {
    setBusyRecId(rec.id);
    try {
      let res;
      if (rec.type === "promo" || rec.type === "merchandising") {
        res = await api.sendCampaign({
          merchantId: rec.merchantId,
          recId: rec.id,
          type: rec.type,
          templateId: rec.templateId,
          customerSegment: rec.customerSegment,
          discountPct: rec.discountPct,
          itemId: rec.itemId,
        });
      } else if (rec.type === "reorder" || rec.type === "festival_prep") {
        res = await api.sendReorder({
          merchantId: rec.merchantId,
          recId: rec.id,
          trigger: rec.type === "festival_prep" ? "festival_prep" : "low_stock",
          itemId: rec.itemId,
          quantity: rec.recommendedQty,
        });
      } else if (rec.type === "loan") {
        res = await api.applyLoan({
          merchantId: rec.merchantId,
          recId: rec.id,
          tier: rec.tier,
          amountInr: rec.suggestedApplyAmount,
          consent: true,
        });
      } else if (rec.type === "insurance") {
        res = await api.applyInsurance({
          merchantId: rec.merchantId,
          recId: rec.id,
          productName: rec.productName,
          premiumInr: rec.premiumInr,
          consent: true,
        });
      }
      applyActionResult(rec.id, res);
      refreshCatalog(rec.merchantId);
    } catch (err) {
      setResults((prev) => ({ ...prev, [rec.id]: { status: "blocked", reason: err.message } }));
    } finally {
      setBusyRecId(null);
    }
  };

  const handleDismiss = async (rec) => {
    setBusyRecId(rec.id);
    try {
      await api.dismissRecommendation(rec.merchantId, rec.id);
      setResults((prev) => ({ ...prev, [rec.id]: { status: "dismissed" } }));
      refreshAudit(rec.merchantId);
    } finally {
      setBusyRecId(null);
    }
  };

  const handleEnd = async (rec) => {
    setBusyRecId(rec.id);
    try {
      const res = await api.endRecommendation(rec.merchantId, rec.id);
      setResults((prev) => ({ ...prev, [rec.id]: { status: "ended", reason: res.auditEntry?.reason } }));
      refreshAudit(rec.merchantId);
    } finally {
      setBusyRecId(null);
    }
  };

  const handleChatSend = async (message) => {
    setChatMessages((prev) => [...prev, { role: "merchant", text: message }]);
    setChatBusy(true);
    try {
      const res = await api.sendChat(selectedId, message);
      setChatMessages((prev) => [...prev, { role: "vriddhi", text: res.reply }]);

      if (res.actionedRecId && res.actionResult) {
        setResults((prev) => ({
          ...prev,
          [res.actionedRecId]: {
            status: res.actionResult.status,
            reason: res.actionResult.auditEntry?.reason,
            guardrailChecks: res.actionResult.guardrailChecks,
          },
        }));
        refreshAudit(selectedId);
      }
      if (res.newRecommendations?.length) {
        setRecommendations((prev) => {
          const existingIds = new Set(prev.map((r) => r.id));
          const additions = res.newRecommendations.filter((r) => !existingIds.has(r.id));
          return additions.length ? [...prev, ...additions] : prev;
        });
      }
      // Chat can change stock (record_stock_sold/restocked) or execute an
      // approve/end action — cheap enough to just always refresh so the
      // Dashboard is never stale when the merchant switches back to it.
      refreshCatalog(selectedId);
    } finally {
      setChatBusy(false);
    }
  };

  const activeRecs = recommendations.filter((r) => !results[r.id] || results[r.id].status === "blocked");
  const finishedRecs = recommendations.filter((r) => results[r.id] && results[r.id].status !== "blocked");
  const pendingCount = activeRecs.length;

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>
          <span className="brand">Vriddhi</span>
        </h1>
        <nav className="view-switcher">
          <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}>
            Dashboard
          </button>
          <button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}>
            Chat
            {pendingCount > 0 && <span className="nav-badge">{pendingCount}</span>}
          </button>
        </nav>
        <MerchantSwitcher merchants={merchants} selectedId={selectedId} onChange={setSelectedId} />
      </header>

      {view === "dashboard" ? (
        <div className="view-dashboard">
          <div className="dashboard-grid">
            {merchant && (
              <div className="section-card span-2">
                <div className="merchant-headline">
                  <div>
                    <div className="merchant-name">{merchant.name}</div>
                    <div className="merchant-sub">{merchant.category.replace(/_/g, " ")} · {merchant.city}, {merchant.state}</div>
                  </div>
                  <div className="scan-actions">
                    <button className="primary" disabled={scanning} onClick={() => runScan(selectedId)}>
                      {scanning ? "Scanning…" : "Run diagnostic scan"}
                    </button>
                    <button className="ghost" onClick={() => setShowAudit(true)}>Audit log</button>
                  </div>
                </div>
                <div className="stat-row">
                  <div className="stat-tile">
                    <div className="stat-label">Avg monthly settlement</div>
                    <div className="stat-value">₹{merchant.avg_monthly_settlement_inr.toLocaleString("en-IN")}</div>
                  </div>
                  <div className="stat-tile">
                    <div className="stat-label">Consistency score</div>
                    <div className="stat-value">{merchant.settlement_consistency_score}</div>
                  </div>
                  <div className="stat-tile">
                    <div className="stat-label">Pending recommendations</div>
                    <div className="stat-value">{pendingCount}</div>
                  </div>
                </div>
              </div>
            )}

            <div className="section-card span-2">
              <h2>Revenue trend (90 days)</h2>
              <RevenueChart transactions={transactions} />
            </div>

            <div className="section-card span-2">
              <h2>Recommendations</h2>
              {activeRecs.length === 0 && finishedRecs.length === 0 && (
                <div className="empty-state">
                  {scanning ? "Scanning for opportunities…" : "No recommendations yet — run a diagnostic scan."}
                </div>
              )}
              {activeRecs.map((rec) => (
                <RecommendationCard
                  key={rec.id}
                  rec={rec}
                  onApprove={handleApprove}
                  onDismiss={handleDismiss}
                  onEnd={handleEnd}
                  busy={busyRecId === rec.id}
                  result={results[rec.id]}
                />
              ))}
              {finishedRecs.map((rec) => (
                <RecommendationCard
                  key={rec.id}
                  rec={rec}
                  onApprove={handleApprove}
                  onDismiss={handleDismiss}
                  onEnd={handleEnd}
                  busy={busyRecId === rec.id}
                  result={results[rec.id]}
                />
              ))}
            </div>

            <div className="section-card">
              <h2>Catalog / inventory status</h2>
              <CatalogStatus catalog={catalog} />
            </div>

            <div className="section-card">
              <h2>Recent customer feedback</h2>
              <FeedbackList feedback={feedback} />
            </div>
          </div>
        </div>
      ) : (
        <div className="view-chat">
          <div className="chat-column">
            <ChatPanel messages={chatMessages} onSend={handleChatSend} busy={chatBusy} />
          </div>
        </div>
      )}

      {showAudit && <AuditLogModal entries={auditEntries} onClose={() => setShowAudit(false)} />}
    </div>
  );
}
