import { defineConfig } from "vitest/config";

// G4: failure injection against a real local HTTP server (make test-failure). Needs dist/ for the
// process-exit tests, so the npm script builds first.
export default defineConfig({
  test: { include: ["test/failure/**/*.test.ts"], environment: "node", testTimeout: 30_000, fileParallelism: false },
});
