import { DeliveryDetailPage } from "@/components/deliveries";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <DeliveryDetailPage id={id} />;
}
