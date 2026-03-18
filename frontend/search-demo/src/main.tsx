// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import React from 'react';
import ReactDOM from 'react-dom/client';
import { Amplify } from 'aws-amplify';
import App from './App';
import { amplifyConfig, missingConfig } from './config';
import './index.css';

const root = ReactDOM.createRoot(document.getElementById('root')!);

if (missingConfig.length > 0) {
  // Without these, Amplify signs requests against the wrong endpoint and fails
  // with an opaque error. Fail loudly instead.
  root.render(
    <main className="mx-auto max-w-xl p-8 font-sans">
      <h1 className="mb-2 text-xl font-bold">Configuration missing</h1>
      <p className="mb-4">
        The demo needs these environment variables at build time:
      </p>
      <ul className="mb-4 list-disc pl-6">
        {missingConfig.map((name) => (
          <li key={name}>
            <code>{name}</code>
          </li>
        ))}
      </ul>
      <p>
        Run <code>./scripts/test-search.sh</code> from the repository root to
        generate <code>frontend/search-demo/.env</code> from the deployed stack,
        then rebuild.
      </p>
    </main>
  );
} else {
  Amplify.configure(amplifyConfig);
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
