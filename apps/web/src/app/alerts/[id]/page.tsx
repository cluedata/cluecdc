import { AlertDetailPage } from "@/components/alerts";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <AlertDetailPage id={id} />;
}
