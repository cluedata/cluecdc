import { SchemasPage } from "@/components/operations";
export default async function Page({
  params,
}: {
  params: Promise<{ sourceId: string; schema: string; table: string }>;
}) {
  const { sourceId, schema, table } = await params;
  return <SchemasPage sourceId={sourceId} schema={schema} table={table} />;
}
