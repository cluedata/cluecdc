import { TopicsPage } from "@/components/stream";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ name: string }>;
  searchParams: Promise<{ cluster?: string }>;
}) {
  const { name } = await params;
  const { cluster } = await searchParams;
  return <TopicsPage name={name} initialCluster={cluster || ""} />;
}
