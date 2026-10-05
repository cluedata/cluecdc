import { TopicsPage } from "@/components/stream";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ cluster?: string }>;
}) {
  const { cluster } = await searchParams;
  return <TopicsPage initialCluster={cluster || ""} />;
}
