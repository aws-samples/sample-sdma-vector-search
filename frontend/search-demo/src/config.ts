// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Values are written to .env by scripts/test-search.sh, which resolves them
// from the deployed stack. There is no safe default for region: guessing one
// makes the app sign requests for the wrong endpoint and fail obscurely, so
// leave it empty and let the missing-config check below surface the problem.
import type { ResourcesConfig } from 'aws-amplify';

export const config = {
  cognito: {
    userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID || '',
    clientId: import.meta.env.VITE_COGNITO_CLIENT_ID || '',
    region: import.meta.env.VITE_AWS_REGION || '',
  },
  api: {
    endpoint: import.meta.env.VITE_API_ENDPOINT || '',
  },
};

/**
 * Required configuration that is missing. Empty when the app is configured.
 */
export const missingConfig: string[] = [
  ['VITE_COGNITO_USER_POOL_ID', config.cognito.userPoolId],
  ['VITE_COGNITO_CLIENT_ID', config.cognito.clientId],
  ['VITE_AWS_REGION', config.cognito.region],
  ['VITE_API_ENDPOINT', config.api.endpoint],
]
  .filter(([, value]) => !value)
  .map(([name]) => name);

// The identity pool is deliberately absent. This demo authenticates with the
// user pool and sends the resulting JWT as a bearer token, which is what the
// API's Cognito authorizer checks. An identity pool would only be needed to
// obtain AWS credentials and sign requests, which nothing here does -- it used
// to be configured anyway, and only ever produced an unused value.
export const amplifyConfig: ResourcesConfig = {
  Auth: {
    Cognito: {
      userPoolId: config.cognito.userPoolId,
      userPoolClientId: config.cognito.clientId,
    },
  },
  API: {
    REST: {
      extension: {
        endpoint: config.api.endpoint,
        region: config.cognito.region,
      },
    },
  },
};
