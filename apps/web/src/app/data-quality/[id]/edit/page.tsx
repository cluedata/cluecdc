import { CheckEditor } from "@/features/data-quality/editor";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <CheckEditor id={id} />;
}
