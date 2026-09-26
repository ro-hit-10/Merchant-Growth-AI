export default function MerchantSwitcher({ merchants, selectedId, onChange }) {
  return (
    <div className="merchant-switcher">
      <select value={selectedId || ""} onChange={(e) => onChange(e.target.value)}>
        {merchants.map((m) => (
          <option key={m.merchant_id} value={m.merchant_id}>
            {m.name} ({m.merchant_id})
          </option>
        ))}
      </select>
    </div>
  );
}
