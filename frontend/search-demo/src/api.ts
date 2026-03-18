// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { get, post } from 'aws-amplify/api';
import { fetchAuthSession } from 'aws-amplify/auth';
import { SearchFilters, CategoryConfig } from './types';

export interface SearchResult {
  assetId: string;
  assetName?: string;
  title?: string;
  description?: string;
  category?: string;
  subcategory?: string;
  style?: string;
  score?: number;
  thumbnailUrl?: string;
  structuredMetadata?: {
    category?: string;
    subcategory?: string;
    style?: string;
    materials?: string[];
    primaryColors?: string[];
  };
}

async function getAuthToken(): Promise<string | null> {
  try {
    const session = await fetchAuthSession();
    return session.tokens?.idToken?.toString() || null;
  } catch {
    return null;
  }
}

export async function searchAssets(
  query: string,
  filters?: SearchFilters,
  // Same default the API documents and applies when limit is omitted.
  limit = 10
): Promise<SearchResult[]> {
  const token = await getAuthToken();

  // Remove empty filter values
  const cleanFilters = filters
    ? Object.fromEntries(Object.entries(filters).filter(([_, v]) => v))
    : {};

  // Amplify's DocumentType does not permit undefined property values, so omit
  // `filters` entirely rather than sending it as undefined. An empty query is
  // omitted for the same reason, and because the API treats its absence as
  // browse: list the assets, filtered, without ranking them.
  const trimmed = query.trim();
  const body = {
    ...(trimmed ? { query: trimmed } : {}),
    limit,
    ...(Object.keys(cleanFilters).length > 0 ? { filters: cleanFilters } : {}),
  };

  const response = await post({
    apiName: 'extension',
    path: '/assets/search',
    options: {
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body,
    },
  }).response;

  const data = (await response.body.json()) as unknown as { results?: SearchResult[] };
  return data.results || [];
}

export async function fetchCategories(): Promise<CategoryConfig> {
  const token = await getAuthToken();

  const response = await get({
    apiName: 'extension',
    path: '/categories',
    options: {
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    },
  }).response;

  const data = (await response.body.json()) as unknown as CategoryConfig & { success?: boolean };
  return {
    categories: data.categories || {},
    styles: data.styles || [],
    materials: data.materials || [],
    colors: data.colors || [],
  };
}
