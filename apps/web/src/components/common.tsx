"use client";
import Link from "next/link";
import {
  useState,
  useId,
  cloneElement,
  isValidElement,
  type ReactElement,
} from "react";
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  useReactTable,
} from "@tanstack/react-table";
import {
  AlertCircle,
  ArrowDownUp,
  ChevronLeft,
  ChevronRight,
  Database,
  Search,
} from "lucide-react";
import { Button, Input } from "@cluecdc/ui";
import { ApiError } from "@/lib/api";

export { StatusBadge as Status } from "./operational";
import { Skeleton } from "./operational";

export function PageHeader({
  title,
  description,
  eyebrow,
  children,
}: React.PropsWithChildren<{
  title: string;
  description: string;
  eyebrow?: string;
}>) {
  return (
    <div className="page-header">
      <div>
        <div className="eyebrow">{eyebrow || "DATA MOVEMENT"}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="header-actions">{children}</div>
    </div>
  );
}
export function Loading() {
  return <Skeleton />;
}
export function ErrorPanel({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="error-panel" role="alert">
      <AlertCircle size={20} />
      <div>
        <strong>
          {error instanceof ApiError ? error.code : "Operation failed"}
        </strong>
        <p>{error instanceof Error ? error.message : "Unable to load data"}</p>
        {error instanceof ApiError && error.correlationId && (
          <small>Correlation ID: {error.correlationId}</small>
        )}
        {error instanceof ApiError && !!error.details && (
          <details>
            <summary>Details</summary>
            <pre>{JSON.stringify(error.details, null, 2)}</pre>
          </details>
        )}
      </div>
      {retry && (
        <Button variant="outline" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
export function Empty({
  title,
  description,
  href,
  action,
  onAction,
}: {
  title: string;
  description: string;
  href?: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Database size={24} />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {onAction && (
        <Button onClick={onAction}>{action || "Get started"}</Button>
      )}
      {href && (
        <Button asChild>
          <Link href={href}>{action || "Get started"}</Link>
        </Button>
      )}
    </div>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}
export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: string[];
  active: string;
  onChange: (tab: string) => void;
}) {
  return (
    <div className="tabs" role="tablist" aria-label="Resource sections">
      {tabs.map((tab) => (
        <button
          role="tab"
          aria-selected={active === tab}
          tabIndex={active === tab ? 0 : -1}
          onKeyDown={(event) => {
            const current = tabs.indexOf(tab);
            const index =
              event.key === "ArrowRight"
                ? (current + 1) % tabs.length
                : event.key === "ArrowLeft"
                  ? (current - 1 + tabs.length) % tabs.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? tabs.length - 1
                      : -1;
            if (index < 0) return;
            event.preventDefault();
            onChange(tabs[index]);
            event.currentTarget.parentElement
              ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
              [index]?.focus();
          }}
          key={tab}
          onClick={() => onChange(tab)}
          className={active === tab ? "tab-active" : ""}
        >
          {tab}
        </button>
      ))}
    </div>
  );
}
export function Field({
  label,
  error,
  children,
  hint,
}: React.PropsWithChildren<{ label: string; error?: string; hint?: string }>) {
  const id = useId();
  const descriptionId = `${id}-description`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {isValidElement(children)
        ? cloneElement(
            children as ReactElement<{
              id: string;
              "aria-describedby"?: string;
              "aria-invalid"?: boolean;
            }>,
            {
              id,
              "aria-describedby": error || hint ? descriptionId : undefined,
              "aria-invalid": !!error,
            },
          )
        : children}
      {error ? (
        <small id={descriptionId} className="field-error">
          {error}
        </small>
      ) : hint ? (
        <small id={descriptionId}>{hint}</small>
      ) : null}
    </div>
  );
}
export function DataTable<T>({
  data,
  columns,
  search = true,
  getRowHref,
  onRowClick,
  emptyTitle = "No matching records",
}: {
  data: T[];
  columns: ColumnDef<T>[];
  search?: boolean;
  getRowHref?: (row: T) => string;
  onRowClick?: (row: T) => void;
  emptyTitle?: string;
}) {
  const [filter, setFilter] = useState("");
  // TanStack Table manages mutable row models; React Compiler is intentionally skipped.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data,
    columns,
    state: { globalFilter: filter },
    onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 15 } },
  });
  return (
    <div className="data-table">
      <div className="table-toolbar">
        {search && (
          <>
            <div className="table-search">
              <Search size={15} />
              <Input
                aria-label="Filter table"
                placeholder="Filter records…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </div>
            <span className="muted">
              {table.getFilteredRowModel().rows.length} records
            </span>
          </>
        )}
        <details className="column-menu">
          <summary>Columns</summary>
          <div>
            {table
              .getAllLeafColumns()
              .filter(
                (column) =>
                  typeof column.columnDef.header === "string" &&
                  column.columnDef.header,
              )
              .map((column) => (
                <label key={column.id}>
                  <input
                    type="checkbox"
                    checked={column.getIsVisible()}
                    onChange={column.getToggleVisibilityHandler()}
                  />
                  {column.columnDef.header as string}
                </label>
              ))}
          </div>
        </details>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => (
                  <th
                    key={header.id}
                    aria-sort={
                      header.column.getIsSorted() === "asc"
                        ? "ascending"
                        : header.column.getIsSorted() === "desc"
                          ? "descending"
                          : "none"
                    }
                  >
                    <button
                      disabled={!header.column.getCanSort()}
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                      {header.column.getCanSort() && <ArrowDownUp size={12} />}
                    </button>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className={
                  getRowHref || onRowClick ? "clickable-row" : undefined
                }
                tabIndex={getRowHref || onRowClick ? 0 : undefined}
                aria-label={
                  getRowHref || onRowClick
                    ? `Open record ${row.index + 1}`
                    : undefined
                }
                onClick={(event) => {
                  if (
                    (event.target as HTMLElement).closest(
                      "a,button,input,select,textarea,summary",
                    )
                  )
                    return;
                  if (onRowClick) onRowClick(row.original);
                  else if (getRowHref)
                    window.location.assign(getRowHref(row.original));
                }}
                onKeyDown={(event) => {
                  if (
                    event.target !== event.currentTarget ||
                    event.key !== "Enter"
                  )
                    return;
                  event.preventDefault();
                  if (onRowClick) onRowClick(row.original);
                  else if (getRowHref)
                    window.location.assign(getRowHref(row.original));
                }}
              >
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className={
                      /topic|offset|lsn|timestamp|created_at|updated_at|throughput|lag|partition|replication|port|host|database_name|bootstrap|schema_hash|schema_name|table_name|message_rate|estimated|records_per|last_|tables|destinations|version|size/.test(
                        cell.column.id,
                      )
                        ? "technical-value"
                        : undefined
                    }
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!table.getRowModel().rows.length && (
          <div className="table-empty">
            <strong>{emptyTitle}</strong>
            <span>Adjust your search or filters to view records.</span>
          </div>
        )}
      </div>
      <div className="table-footer">
        <span>
          {table.getFilteredRowModel().rows.length} records · Page{" "}
          {table.getState().pagination.pageIndex + 1} of{" "}
          {Math.max(1, table.getPageCount())}
        </span>
        <div>
          <Button
            variant="ghost"
            aria-label="Previous page"
            disabled={!table.getCanPreviousPage()}
            onClick={() => table.previousPage()}
          >
            <ChevronLeft size={16} />
          </Button>
          <Button
            variant="ghost"
            aria-label="Next page"
            disabled={!table.getCanNextPage()}
            onClick={() => table.nextPage()}
          >
            <ChevronRight size={16} />
          </Button>
        </div>
      </div>
    </div>
  );
}
