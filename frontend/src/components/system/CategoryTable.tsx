import type { CategoryCount } from "@/lib/types";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface CategoryTableProps {
  categories: CategoryCount[];
}

export function CategoryTable({ categories }: CategoryTableProps) {
  const total = categories.reduce((sum, c) => sum + c.count, 0);

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Category</TableHead>
          <TableHead className="text-right">Count</TableHead>
          <TableHead className="text-right">%</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {categories.map((cat) => (
          <TableRow key={cat.category}>
            <TableCell className="capitalize">
              {cat.category.replace(/_/g, " ")}
            </TableCell>
            <TableCell className="text-right">{cat.count}</TableCell>
            <TableCell className="text-right">
              {total > 0 ? ((cat.count / total) * 100).toFixed(1) : "0.0"}%
            </TableCell>
          </TableRow>
        ))}
        {total > 0 && (
          <TableRow className="font-medium">
            <TableCell>Total</TableCell>
            <TableCell className="text-right">{total}</TableCell>
            <TableCell className="text-right">100.0%</TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}
