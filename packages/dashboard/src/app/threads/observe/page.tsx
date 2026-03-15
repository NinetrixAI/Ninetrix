import { Suspense } from "react";
import ObservabilityClient from "./ObservabilityClient";

export default function ObservePage() {
  return (
    <Suspense>
      <ObservabilityClient />
    </Suspense>
  );
}
