export default function FeedbackList({ feedback }) {
  if (feedback.length === 0) {
    return <div className="empty-state">No feedback yet.</div>;
  }
  const sorted = [...feedback].sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 6);
  return (
    <div>
      {sorted.map((f, i) => (
        <div className="feedback-row" key={i}>
          <span className="feedback-rating">{"★".repeat(f.rating)}{"☆".repeat(5 - f.rating)}</span>
          {f.comment}
          <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 2 }}>
            {f.date} · {f.channel}
          </div>
        </div>
      ))}
    </div>
  );
}
