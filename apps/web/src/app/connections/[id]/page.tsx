import { ConnectionDetailPage } from "@/components/connections";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ConnectionDetailPage id={id} />;
}
