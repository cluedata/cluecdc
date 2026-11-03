import { CheckPage } from "@/features/data-quality/pages";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <CheckPage id={id} />;
}
