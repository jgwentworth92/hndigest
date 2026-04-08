import type { CategoryCount } from "@/lib/types";

interface CategoryTableProps {
  categories: CategoryCount[];
}

export function CategoryTable({ categories }: CategoryTableProps) {
  const total = categories.reduce((sum, c) => sum + c.count, 0);

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-gray-200 text-left text-gray-500">
          <th className="py-2 font-medium">Category</th>
          <th className="py-2 font-medium text-right">Count</th>
          <th className="py-2 font-medium text-right">%</th>
        </tr>
      </thead>
      <tbody>
        {categories.map((cat) => (
          <tr key={cat.category} className="border-b border-gray-100">
            <td className="py-2 capitalize">
              {cat.category.replace(/_/g, " ")}
            </td>
            <td className="py-2 text-right">{cat.count}</td>
            <td className="py-2 text-right">
              {total > 0 ? ((cat.count / total) * 100).toFixed(1) : "0.0"}%
            </td>
          </tr>
        ))}
      </tbody>
      {total > 0 && (
        <tfoot>
          <tr className="font-medium">
            <td className="py-2">Total</td>
            <td className="py-2 text-right">{total}</td>
            <td className="py-2 text-right">100.0%</td>
          </tr>
        </tfoot>
      )}
    </table>
  );
}
