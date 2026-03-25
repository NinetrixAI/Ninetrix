import { Suspense } from "react";
import RunDetailPage from "./DetailClient";

export default function Page() {
  return (
    <Suspense fallback={<div style={{ padding: 32, color: "var(--text-muted)" }}>Loading...</div>}>
      <RunDetailPage />
    </Suspense>
  );
}
