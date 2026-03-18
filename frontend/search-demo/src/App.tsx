// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useState, useEffect, useRef } from 'react';
import { Authenticator } from '@aws-amplify/ui-react';
// The monolithic stylesheet is intentional. Amplify UI also publishes
// per-component layers, but Authenticator composes many primitives (fields,
// inputs, tabs, buttons, password field, visually-hidden labels), so importing
// only reset/base/authenticator renders an unstyled form. Enumerating the
// primitives it happens to use couples this app to Amplify's internal
// composition and breaks silently when that changes.
import '@aws-amplify/ui-react/styles.css';
import { searchAssets, fetchCategories, SearchResult } from './api';
import { SearchFilters, CategoryConfig, DEFAULT_CATEGORY_CONFIG } from './types';

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value?: string;
  options: string[];
  onChange: (value?: string) => void;
}) {
  return (
    <div>
      <label className="block text-sm text-gray-600 mb-1">{label}</label>
      <select
        value={value || ''}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
      >
        <option value="">All</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    </div>
  );
}

function FilterPanel({
  filters,
  categoryConfig,
  onChange,
  onClear,
}: {
  filters: SearchFilters;
  categoryConfig: CategoryConfig;
  onChange: (filters: SearchFilters) => void;
  onClear: () => void;
}) {
  const { categories, styles, materials, colors } = categoryConfig;
  const subcategories = filters.category ? categories[filters.category] || [] : [];

  return (
    <div className="w-60 flex-shrink-0 bg-white border-r border-gray-200 p-4 space-y-4">
      <h2 className="font-semibold text-gray-700">Filters</h2>

      <FilterSelect
        label="Category"
        value={filters.category}
        options={Object.keys(categories)}
        onChange={(v) => onChange({ ...filters, category: v, subcategory: undefined })}
      />

      {subcategories.length > 0 && (
        <FilterSelect
          label="Subcategory"
          value={filters.subcategory}
          options={subcategories}
          onChange={(v) => onChange({ ...filters, subcategory: v })}
        />
      )}

      <FilterSelect
        label="Style"
        value={filters.style}
        options={styles}
        onChange={(v) => onChange({ ...filters, style: v })}
      />

      <FilterSelect
        label="Materials"
        value={filters.materials}
        options={materials}
        onChange={(v) => onChange({ ...filters, materials: v })}
      />

      <FilterSelect
        label="Colors"
        value={filters.primaryColors}
        options={colors}
        onChange={(v) => onChange({ ...filters, primaryColors: v })}
      />

      <button
        onClick={onClear}
        className="w-full py-2 text-sm text-gray-600 hover:text-gray-900 border border-gray-300 rounded-lg hover:bg-gray-50"
      >
        Clear Filters
      </button>
    </div>
  );
}

function ActiveFilters({
  filters,
  onRemove,
}: {
  filters: SearchFilters;
  onRemove: (key: keyof SearchFilters) => void;
}) {
  const activeFilters = Object.entries(filters).filter(([_, v]) => v) as [keyof SearchFilters, string][];

  if (activeFilters.length === 0) return null;

  const labelMap: Record<keyof SearchFilters, string> = {
    category: 'Category',
    subcategory: 'Subcategory',
    style: 'Style',
    materials: 'Materials',
    primaryColors: 'Colors',
  };

  return (
    <div className="flex flex-wrap gap-2 mb-4">
      {activeFilters.map(([key, value]) => (
        <span
          key={key}
          className="inline-flex items-center px-2 py-1 bg-orange-100 text-orange-700 rounded text-sm"
        >
          {labelMap[key]}: {value}
          <button
            onClick={() => onRemove(key)}
            className="ml-1 hover:text-orange-900"
            aria-label={`Remove ${labelMap[key]} filter`}
          >
            <span aria-hidden="true">×</span>
          </button>
        </span>
      ))}
    </div>
  );
}

type SortOption = 'score' | 'name-asc' | 'name-desc' | 'category';

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: 'score', label: 'Relevance' },
  { value: 'name-asc', label: 'Name (A-Z)' },
  { value: 'name-desc', label: 'Name (Z-A)' },
  { value: 'category', label: 'Category' },
];

// Browse results carry no score, so there is nothing for Relevance to order by.
const BROWSE_SORT_OPTIONS = SORT_OPTIONS.filter((o) => o.value !== 'score');

function sortResults(results: SearchResult[], sortBy: SortOption): SearchResult[] {
  const sorted = [...results];
  switch (sortBy) {
    case 'score':
      // score is a similarity in 0-1, so higher is a closer match. The API
      // already returns results in this order; sorting keeps it stable if the
      // user switches away and back.
      return sorted.sort((a, b) => (b.score || 0) - (a.score || 0));
    case 'name-asc':
      return sorted.sort((a, b) =>
        (a.assetName || a.title || '').localeCompare(b.assetName || b.title || '')
      );
    case 'name-desc':
      return sorted.sort((a, b) =>
        (b.assetName || b.title || '').localeCompare(a.assetName || a.title || '')
      );
    case 'category':
      return sorted.sort((a, b) => {
        const catA = a.structuredMetadata?.category || a.category || '';
        const catB = b.structuredMetadata?.category || b.category || '';
        return catA.localeCompare(catB);
      });
    default:
      return sorted;
  }
}

function SearchDemo() {
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState<SearchFilters>({});
  const [results, setResults] = useState<SearchResult[]>([]);
  const [sortBy, setSortBy] = useState<SortOption>('score');
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  // Distinguishes "the search failed" from "the search found nothing". Without
  // it both rendered as "No results found", so a network, auth or server error
  // looked like an empty catalogue.
  const [error, setError] = useState<string | null>(null);
  const [categoryConfig, setCategoryConfig] = useState<CategoryConfig>(DEFAULT_CATEGORY_CONFIG);

  // Browse mode is whatever the last request was, not what is typed now: the
  // input can be edited before the debounce fires, and the header must describe
  // the results on screen. Results carry a score only when they were ranked.
  const isBrowsing = results.length > 0 && typeof results[0].score !== 'number';
  const sortOptions = isBrowsing ? BROWSE_SORT_OPTIONS : SORT_OPTIONS;
  // Relevance stays selected across a switch into browse, so fall back for
  // display and sorting without discarding the user's choice.
  const effectiveSortBy: SortOption =
    isBrowsing && sortBy === 'score' ? 'name-asc' : sortBy;
  const sortedResults = sortResults(results, effectiveSortBy);

  const executeSearch = async (searchQuery: string, searchFilters: SearchFilters) => {
    // An empty query is not an error: the API lists assets instead of ranking
    // them, so browsing and filter-only narrowing both work. What must not
    // happen is substituting a placeholder like '*', which gets embedded
    // literally and orders results by their distance to that character.
    setLoading(true);
    setSearched(true);
    setError(null);
    try {
      const data = await searchAssets(searchQuery, searchFilters);
      setResults(data);
    } catch (err) {
      // Surface it. Returning an empty list here made a failure indistinguishable
      // from a catalogue with nothing in it.
      console.error('Search failed:', err);
      setResults([]);
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  };

  // Track if initial search has been done
  const isInitialMount = useRef(true);

  // Fetch categories and list the catalogue on mount. The initial listing is
  // browse mode -- no query, so no ranking and no score -- rather than the
  // placeholder '*' search this used to run, whose ordering was meaningless.
  useEffect(() => {
    const init = async () => {
      try {
        const config = await fetchCategories();
        // Only replace the built-in vocabulary if the response actually has
        // one. An empty payload used to overwrite it, leaving every filter
        // dropdown with just its "All" entry and no way to tell why.
        if (Object.keys(config.categories || {}).length > 0) {
          setCategoryConfig(config);
        } else {
          console.warn('Category config was empty; keeping built-in filter options');
        }
      } catch (err) {
        console.error('Failed to fetch categories:', err);
      }
      executeSearch('', {});
    };
    init();
  }, []);

  // Auto-search when filters/query change (skip initial mount)
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }

    const timer = setTimeout(() => {
      executeSearch(query, filters);
    }, 300);

    return () => clearTimeout(timer);
  }, [filters, query]);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    executeSearch(query, filters);
  };

  const handleFilterChange = (newFilters: SearchFilters) => {
    setFilters(newFilters);
  };

  const handleClearFilters = () => {
    setFilters({});
  };

  const handleRemoveFilter = (key: keyof SearchFilters) => {
    const newFilters = { ...filters };
    delete newFilters[key];
    if (key === 'category') {
      delete newFilters.subcategory;
    }
    setFilters(newFilters);
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200">
        <div className="px-4 py-4">
          <h1 className="text-xl font-bold text-gray-900">
            SDMA Vector Search Demo
          </h1>
        </div>
      </header>

      <div className="flex">
        <FilterPanel
          filters={filters}
          categoryConfig={categoryConfig}
          onChange={handleFilterChange}
          onClear={handleClearFilters}
        />

        <main className="flex-1 px-6 py-8">
          <form onSubmit={handleSearch} className="mb-6">
            <div className="flex gap-3">
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search 3D assets... (e.g., modern wooden chair)"
                className="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
              />
              <button
                type="submit"
                disabled={loading}
                className="px-6 py-3 bg-orange-500 text-white font-medium rounded-lg hover:bg-orange-600 disabled:opacity-50"
              >
                {loading ? 'Searching...' : 'Search'}
              </button>
            </div>
          </form>

          <ActiveFilters filters={filters} onRemove={handleRemoveFilter} />

          {loading ? (
            <div className="text-center py-12 text-gray-500">Searching...</div>
          ) : searched ? (
            <div>
              <div className="flex items-center justify-between mb-4">
                <p className="text-sm text-gray-600">
                  {isBrowsing
                    ? `Showing ${results.length} asset${results.length !== 1 ? 's' : ''}`
                    : `${results.length} result${results.length !== 1 ? 's' : ''} found`}
                </p>
                <div className="flex items-center gap-2">
                  <label className="text-sm text-gray-600">Sort by:</label>
                  <select
                    value={effectiveSortBy}
                    onChange={(e) => setSortBy(e.target.value as SortOption)}
                    className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
                  >
                    {sortOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>
              </div>
              {sortedResults.length > 0 ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                  {sortedResults.map((asset) => (
                    <AssetCard key={asset.assetId} asset={asset} />
                  ))}
                </div>
              ) : error ? (
                <div
                  className="text-center py-12 bg-white rounded-lg border border-red-300"
                  role="alert"
                >
                  <p className="text-red-700 font-medium">Search failed</p>
                  <p className="text-sm text-gray-600 mt-1">{error}</p>
                </div>
              ) : (
                <div className="text-center py-12 bg-white rounded-lg border">
                  <p className="text-gray-500">No results found</p>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-12 bg-white rounded-lg border">
              <p className="text-gray-500">Enter a search query or select filters to find 3D assets</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function AssetCard({ asset }: { asset: SearchResult }) {
  // The API returns score only for a ranked search. It is a cosine similarity
  // in 0-1, shown as its actual value and labelled to match the "Relevance"
  // sort option. It was previously rendered as "N% match", which read as a
  // confidence percentage it never was.
  const relevance = typeof asset.score === 'number' ? asset.score : undefined;
  const metadata = asset.structuredMetadata;
  const category = metadata?.category || asset.category || 'Uncategorized';
  const subcategory = metadata?.subcategory;
  const style = metadata?.style;

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden hover:shadow-md transition-shadow">
      <div className="aspect-square bg-gray-100 flex items-center justify-center overflow-hidden">
        {asset.thumbnailUrl ? (
          <img
            src={asset.thumbnailUrl}
            alt={asset.title || 'Asset'}
            className="w-full h-full object-contain"
          />
        ) : (
          <span className="text-4xl text-gray-300" aria-hidden="true">📦</span>
        )}
      </div>
      <div className="p-4">
        <h3 className="font-medium text-gray-900 truncate">
          {asset.assetName || asset.title || 'Untitled'}
        </h3>
        {asset.description && (
          <p className="text-sm text-gray-500 mt-1 line-clamp-2">
            {asset.description}
          </p>
        )}
        <div className="flex flex-wrap gap-1 mt-3">
          <span className="text-xs text-gray-600 bg-gray-100 px-2 py-0.5 rounded">
            {category}
          </span>
          {subcategory && (
            <span className="text-xs text-gray-600 bg-gray-100 px-2 py-0.5 rounded">
              {subcategory}
            </span>
          )}
          {style && (
            <span className="text-xs text-blue-600 bg-blue-50 px-2 py-0.5 rounded">
              {style}
            </span>
          )}
        </div>
        {relevance !== undefined && (
          <div className="flex justify-end mt-2">
            <span className="text-xs font-medium text-orange-700">
              Relevance {relevance.toFixed(3)}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Authenticator>
      <SearchDemo />
    </Authenticator>
  );
}
