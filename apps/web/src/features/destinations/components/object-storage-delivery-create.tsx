"use client";

import { DestinationWizard } from "./database-delivery-create";

/** Compatibility route: object storage now uses the shared delivery wizard. */
export function ObjectStorageDeliveryCreatePage() {
  return (
    <DestinationWizard deliveryFirst initialDeliveryType="OBJECT_STORAGE" />
  );
}
