import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export default function RevenueChart({ transactions }) {
  const byDate = new Map();
  for (const t of transactions) {
    byDate.set(t.date, (byDate.get(t.date) || 0) + t.amount_inr);
  }
  const data = [...byDate.entries()]
    .sort((a, b) => (a[0] > b[0] ? 1 : -1))
    .map(([date, revenue]) => ({ date: date.slice(5), revenue }));

  if (data.length === 0) {
    return <div className="empty-state">No transaction data available.</div>;
  }

  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e1e6ee" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} interval={Math.floor(data.length / 10)} />
          <YAxis tick={{ fontSize: 11 }} width={50} />
          <Tooltip formatter={(v) => [`₹${v.toLocaleString("en-IN")}`, "Revenue"]} />
          <Line type="monotone" dataKey="revenue" stroke="#00baf2" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
