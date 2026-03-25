import { Suspense } from "react";
import CompareClient from "./CompareClient";

export default function Page() {
  return (
    <Suspense fallback={<div style={{ padding: 32, color: "var(--text-muted)" }}>Loading...</div>}>
      <CompareClient />
    </Suspense>
  );
}
