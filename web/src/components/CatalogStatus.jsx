export default function CatalogStatus({ catalog }) {
  if (catalog.length === 0) {
    return <div className="empty-state">No catalog items.</div>;
  }
  return (
    <table className="catalog-table">
      <thead>
        <tr>
          <th>Item</th>
          <th>Stock</th>
          <th>Reorder at</th>
          <th>Sold (30d)</th>
        </tr>
      </thead>
      <tbody>
        {catalog.map((item) => {
          const low = item.reorder_threshold > 0 && item.stock_qty <= item.reorder_threshold * 1.5;
          return (
            <tr key={item.item_id} className={low ? "low-stock" : ""}>
              <td>{item.item_name}</td>
              <td>{item.stock_qty}</td>
              <td>{item.reorder_threshold || "—"}</td>
              <td>{item.units_sold_last_30d}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
