import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, searchApi, statsApi } from "./api";
import type { SearchResponse, StatsResponse } from "./types";

type Status = "idle" | "loading" | "success" | "error";

function domainFromResult(url: string, domain: string): string {
  if (domain) return domain;
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

export default function App() {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [lastQuery, setLastQuery] = useState<string>("");
  const [stats, setStats] = useState<StatsResponse | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);

  const runSearch = useCallback((rawQuery: string) => {
    const trimmed = rawQuery.trim();
    if (!trimmed) {
      return;
    }

    // Guard against overlapping requests: cancel whatever is in flight first.
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setStatus("loading");
    setErrorMessage("");
    setLastQuery(trimmed);

    searchApi(trimmed, 10, controller.signal)
      .then((data) => {
        setResponse(data);
        setStatus("success");
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          // A newer search superseded this one; nothing to do.
          return;
        }
        const message =
          err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
        setErrorMessage(message);
        setStatus("error");
      });
  }, []);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    runSearch(query);
  };

  const handleRetry = () => {
    runSearch(lastQuery);
  };

  useEffect(() => {
    const controller = new AbortController();
    statsApi(controller.signal).then(setStats);
    return () => controller.abort();
  }, []);

  const results = response?.results ?? [];
  const hasSearched = status === "success" || status === "error";

  return (
    <div className="page">
      <header className="header">
        <h1 className="brand">wifey search</h1>
        {stats && (
          <p className="stats-line">
            Searching {stats.total_docs.toLocaleString()} page
            {stats.total_docs === 1 ? "" : "s"}
          </p>
        )}
      </header>

      <form className="search-form" onSubmit={handleSubmit}>
        <input
          className="search-input"
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search the crawled web..."
          aria-label="Search query"
          autoFocus
        />
        <button className="search-button" type="submit" disabled={status === "loading"}>
          Search
        </button>
      </form>

      <main className="results-area">
        {status === "loading" && (
          <div className="state-panel" role="status" aria-live="polite">
            <span className="spinner" aria-hidden="true" />
            <span>Searching...</span>
          </div>
        )}

        {status === "error" && (
          <div className="state-panel error-panel" role="alert">
            <p>{errorMessage}</p>
            <button className="retry-button" onClick={handleRetry} type="button">
              Retry
            </button>
          </div>
        )}

        {status === "success" && results.length === 0 && (
          <div className="state-panel">
            <p>No results found for &ldquo;{response?.query}&rdquo;.</p>
          </div>
        )}

        {status === "success" && results.length > 0 && response && (
          <>
            <p className="results-meta">
              {response.total_matches.toLocaleString()} result
              {response.total_matches === 1 ? "" : "s"} ({response.took_ms.toFixed(1)} ms)
            </p>
            <ul className="results-list">
              {results.map((result) => (
                <li className="result-card" key={result.url}>
                  <a
                    className="result-title"
                    href={result.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {result.title || result.url}
                  </a>
                  <div className="result-domain">
                    {domainFromResult(result.url, result.domain)}
                  </div>
                  <p className="result-snippet">{result.snippet}</p>
                  <div className="result-score">score: {result.score.toFixed(2)}</div>
                </li>
              ))}
            </ul>
          </>
        )}

        {!hasSearched && status !== "loading" && (
          <div className="state-panel hint-panel">
            <p>Enter a query above to search.</p>
          </div>
        )}
      </main>
    </div>
  );
}
