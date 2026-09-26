export default function AuditLogModal({ entries, onClose }) {
  return (
    <div className="audit-modal-backdrop" onClick={onClose}>
      <div className="audit-modal" onClick={(e) => e.stopPropagation()}>
        <div className="audit-modal-header">
          <strong>Audit log</strong>
          <button className="ghost" onClick={onClose}>Close</button>
        </div>
        <div className="audit-modal-body">
          {entries.length === 0 && <div className="empty-state">No actions logged yet for this merchant.</div>}
          {entries.map((e) => (
            <div className="audit-entry" key={e.id}>
              <div className="audit-top">
                <span>{e.actionType}</span>
                <span>{new Date(e.timestamp).toLocaleString()}</span>
              </div>
              <span className={`audit-outcome ${e.outcome}`}>{e.outcome}</span>
              <div style={{ marginTop: 6 }}>{e.reason}</div>
              {e.guardrailChecks?.length > 0 && (
                <div className="audit-checks">
                  {e.guardrailChecks.map((c, i) => (
                    <div key={i} className={c.passed ? "audit-check-pass" : "audit-check-fail"}>
                      {c.passed ? "✓" : "✕"} {c.rule} — {c.detail}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
