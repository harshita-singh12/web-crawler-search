// Types mirroring the backend REST API contract (see api/ service).

export interface SearchResult {
  url: string;
  title: string | null;
  snippet: string;
  score: number;
  domain: string | null;
  pagerank: number;
}

export interface SearchResponse {
  query: string;
  took_ms: number;
  total_matches: number;
  results: SearchResult[];
}

export interface ApiErrorBody {
  detail: string;
}

export interface StatsResponse {
  total_docs: number;
  total_terms: number;
  last_crawl_at: string | null;
}
