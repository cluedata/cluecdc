import { Empty } from "@/components/common";
export default function NotFound() {
  return (
    <Empty
      title="Page not found"
      description="Choose a page from the navigation."
      href="/"
      action="Open overview"
    />
  );
}
