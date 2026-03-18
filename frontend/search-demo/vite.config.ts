// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': '/src',
      },
    },
    server: {
      port: 3000,
      proxy: {
        // SDMA API proxy - requires VITE_SDMA_API_ENDPOINT env var
        ...(env.VITE_SDMA_API_ENDPOINT ? {
          '/api/sdma': {
            target: env.VITE_SDMA_API_ENDPOINT,
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api\/sdma/, ''),
            secure: true,
          },
        } : {}),
        // Extension API proxy - requires VITE_EXTENSION_API_ENDPOINT env var
        ...(env.VITE_EXTENSION_API_ENDPOINT ? {
          '/api/extension': {
            target: env.VITE_EXTENSION_API_ENDPOINT,
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api\/extension/, ''),
            secure: true,
          },
        } : {}),
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
      // The amplify-ui chunk is ~580 kB: Amplify UI's Authenticator plus the
      // Amplify and AWS SDK code it pulls in. That is inherent to using the
      // hosted auth component and cannot be trimmed without replacing it, so
      // raise the threshold rather than leave a warning that gets ignored.
      // App code sits in its own chunk and is a few kB.
      chunkSizeWarningLimit: 600,
      rolldownOptions: {
        output: {
          // The AWS SDK and Amplify dominate the bundle and change only when
          // dependencies are upgraded, so keep them out of the app chunk. The
          // app then re-downloads as a small file on each change instead of
          // invalidating everything.
          advancedChunks: {
            groups: [
              { name: 'amplify-ui', test: /node_modules\/@aws-amplify\/ui-react/ },
              { name: 'aws-sdk', test: /node_modules\/(@aws-sdk|@smithy)/ },
              { name: 'amplify', test: /node_modules\/(aws-amplify|@aws-amplify)/ },
              { name: 'react', test: /node_modules\/(react|react-dom|scheduler)\// },
            ],
          },
        },
      },
    },
  };
});
