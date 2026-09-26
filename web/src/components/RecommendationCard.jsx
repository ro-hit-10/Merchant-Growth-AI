const TYPE_LABELS = {
  promo: "Growth · Promo",
  reorder: "Ops · Restock",
  loan: "Financial Nudge",
  feedback_digest: "Customer Feedback",
  festival_prep: "Ops · Festival Prep",
  merchandising: "Growth · Slow Mover",
  insurance: "Financial Nudge · Insurance",
};

function titleFor(rec) {
  switch (rec.type) {
    case "promo":
      return `${rec.discountPct}% off ${rec.itemName}`;
    case "reorder":
      return `Reorder ${rec.itemName} — ${rec.recommendedQty} units`;
    case "loan":
      return `${rec.tier} tier working-capital loan — up to ₹${rec.maxLoanInr.toLocaleString("en-IN")}`;
    case "feedback_digest":
      return `Weekly feedback digest (${rec.avgRating}★ avg, ${rec.reviewCount} reviews)`;
    case "festival_prep":
      return `${rec.festivalLabel} prep — ${rec.recommendedQty} extra units of ${rec.itemName}`;
    case "merchandising":
      return `${rec.discountPct}% clearance on ${rec.itemName} (${rec.unitsSold30d} sold/30d)`;
    case "insurance":
      return `${rec.productName} — ₹${rec.premiumInr.toLocaleString("en-IN")}/year`;
    default:
      return rec.type;
  }
}

const ACTIVE_STATUSES = new Set(["sent", "created", "approved", "enrolled"]);

export default function RecommendationCard({ rec, onApprove, onDismiss, onEnd, busy, result }) {
  const isInfoOnly = rec.type === "feedback_digest";
  const finalStatus = result?.status;
  const canEnd = ACTIVE_STATUSES.has(finalStatus) && !isInfoOnly;

  return (
    <div className="rec-card">
      <span className={`rec-type ${rec.type}`}>{TYPE_LABELS[rec.type] || rec.type}</span>
      <div className="rec-title">{titleFor(rec)}</div>
      <div className="rec-reasoning">{rec.reasoning}</div>
      {rec.message && <div className="rec-message">"{rec.message}"</div>}

      {finalStatus && (
        <div className={`rec-status ${finalStatus}`}>
          {finalStatus === "sent" && "✓ Promo sent to customer segment."}
          {finalStatus === "created" && "✓ Reorder request created."}
          {finalStatus === "approved" && "✓ Auto-approved."}
          {finalStatus === "escalated" && "⚠ Escalated to relationship manager for review."}
          {finalStatus === "enrolled" && "✓ Enrolled."}
          {finalStatus === "blocked" && `✕ Blocked by guardrail: ${result.reason}`}
          {finalStatus === "dismissed" && "Dismissed."}
          {finalStatus === "ended" && "◼ Ended."}
        </div>
      )}

      {!finalStatus && !isInfoOnly && (
        <div className="rec-buttons">
          <button className="approve" disabled={busy} onClick={() => onApprove(rec)}>
            {busy ? "Working…" : "Approve"}
          </button>
          <button className="dismiss" disabled={busy} onClick={() => onDismiss(rec)}>
            Dismiss
          </button>
        </div>
      )}
      {!finalStatus && isInfoOnly && (
        <div className="rec-buttons">
          <button className="dismiss" disabled={busy} onClick={() => onDismiss(rec)}>
            Acknowledge
          </button>
        </div>
      )}
      {canEnd && (
        <div className="rec-buttons">
          <button className="end" disabled={busy} onClick={() => onEnd(rec)}>
            {busy ? "Working…" : "End offer"}
          </button>
        </div>
      )}
    </div>
  );
}
