/// <reference types="vite/client" />
// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

// The variables config.ts actually reads. test-search.sh writes them into .env
// from the deployed stack's outputs; .env.example documents them.
interface ImportMetaEnv {
  readonly VITE_COGNITO_USER_POOL_ID: string;
  readonly VITE_COGNITO_CLIENT_ID: string;
  readonly VITE_AWS_REGION: string;
  readonly VITE_API_ENDPOINT: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
