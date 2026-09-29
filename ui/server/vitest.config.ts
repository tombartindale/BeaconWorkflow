import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    testTimeout: 180_000,
    hookTimeout: 180_000,
    // Each file starts its own backend on its own copy of example/; run them one at a time.
    fileParallelism: false,
  },
});
