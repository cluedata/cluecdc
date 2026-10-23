import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

vi.mock("../src/components/destinations", () => ({
  DestinationWizard: ({ deliveryFirst }: { deliveryFirst?: boolean }) => (
    <div data-delivery-first={String(deliveryFirst)}>
      Unified delivery wizard
    </div>
  ),
}));

import { DeliveryCreatePage } from "../src/components/deliveries";

it("opens the unified step-by-step wizard without a delivery-type screen", () => {
  render(<DeliveryCreatePage />);

  expect(screen.getByText("Unified delivery wizard")).toHaveAttribute(
    "data-delivery-first",
    "true",
  );
  expect(
    screen.queryByRole("heading", { name: "Choose delivery type" }),
  ).not.toBeInTheDocument();
});
