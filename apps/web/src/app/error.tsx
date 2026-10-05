"use client";
export default function ErrorPage({ retry }: { retry: () => void }) {
  return (
    <div role="alert">
      <h2>Page unavailable</h2>
      <button onClick={retry}>Try again</button>
    </div>
  );
}
