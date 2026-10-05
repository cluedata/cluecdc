import { ConnectionEditPage } from "@/components/connections";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ConnectionEditPage id={id} />;
}
