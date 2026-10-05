import { Suspense } from "react";
import { ObjectStorageDeliveryCreatePage } from "@/features/destinations/components/object-storage-delivery-create";
export default function Page() {
  return (
    <Suspense>
      <ObjectStorageDeliveryCreatePage />
    </Suspense>
  );
}
