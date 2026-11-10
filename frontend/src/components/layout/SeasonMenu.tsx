import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Check, ChevronDown, History, Plus, RefreshCw } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";

import { analyticsQueryKeys } from "../../api/analytics";
import { ApiError } from "../../api/client";
import {
  importSeason,
  importedSeasonsQueryOptions,
  leagueQueryKeys,
} from "../../api/league";
import { useAuth } from "../../auth/AuthProvider";

const CURRENT_YEAR = new Date().getFullYear();
const IMPORT_PROGRESS_UPDATE_MS = 250;
const IMPORT_COMPLETION_PAUSE_MS = 500;

interface ImportProgressStage {
  threshold: number;
  label: string;
  detail: string;
}

const IMPORT_PROGRESS_STAGES: ImportProgressStage[] = [
  {
    threshold: 0,
    label: "Preparing import",
    detail: "Opening the encrypted ESPN connection.",
  },
  {
    threshold: 12,
    label: "Validating season",
    detail: "Confirming that ESPN can provide the requested season.",
  },
  {
    threshold: 28,
    label: "Loading league data",
    detail: "Reading teams, settings, and the season schedule.",
  },
  {
    threshold: 48,
    label: "Importing matchups",
    detail: "Storing completed and scheduled matchups.",
  },
  {
    threshold: 65,
    label: "Importing lineup history",
    detail: "Loading completed player-week records.",
  },
  {
    threshold: 82,
    label: "Calculating optimal lineups",
    detail: "Building the stored optimal-lineup analysis.",
  },
  {
    threshold: 92,
    label: "Finalizing season",
    detail: "Waiting for ESPN and database processing to finish.",
  },
  {
    threshold: 100,
    label: "Import complete",
    detail: "The season was imported successfully.",
  },
];

function importStageForProgress(progress: number): ImportProgressStage {
  return (
    [...IMPORT_PROGRESS_STAGES]
      .reverse()
      .find((stage) => progress >= stage.threshold) ?? IMPORT_PROGRESS_STAGES[0]
  );
}

interface SeasonMenuProps {
  onNavigate?: () => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to import the requested season.";
}

function seasonStatusLabel(status: string | null, isActive: boolean): string {
  if (isActive) {
    return "Active";
  }

  if (status === "completed") {
    return "Completed";
  }

  if (status === "archived") {
    return "Archived";
  }

  if (status === "draft") {
    return "Draft";
  }

  return "Imported";
}

export function SeasonMenu({ onNavigate }: SeasonMenuProps) {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const containerRef = useRef<HTMLDivElement | null>(null);
  const importProgressTimer = useRef<number | null>(null);

  const [open, setOpen] = useState(false);
  const [showImportForm, setShowImportForm] = useState(false);
  const [selectedYear, setSelectedYear] = useState("");
  const [importing, setImporting] = useState(false);
  const [importProgress, setImportProgress] = useState(0);
  const [importError, setImportError] = useState<string | null>(null);
  const [importSuccess, setImportSuccess] = useState<string | null>(null);

  const seasonsQuery = useQuery(importedSeasonsQueryOptions());

  const selectedSeasonId = Number(searchParams.get("season_id"));

  const activeSeason =
    seasonsQuery.data?.seasons.find((season) => season.is_active) ?? null;

  const viewedSeason =
    seasonsQuery.data?.seasons.find(
      (season) =>
        Number.isInteger(selectedSeasonId) &&
        selectedSeasonId > 0 &&
        season.season_id === selectedSeasonId,
    ) ??
    activeSeason ??
    seasonsQuery.data?.seasons[0] ??
    null;

  const isAdministrator =
    auth.status === "authenticated" && auth.user?.role === "admin";

  const importProgressStage = importStageForProgress(importProgress);

  const availableYears = useMemo(() => {
    const importedYears = new Set(
      seasonsQuery.data?.seasons.map((season) => season.year) ?? [],
    );

    return Array.from(
      {
        length: 11,
      },
      (_, index) => CURRENT_YEAR - index,
    ).filter((year) => !importedYears.has(year));
  }, [seasonsQuery.data?.seasons]);

  useEffect(() => {
    return () => {
      if (importProgressTimer.current !== null) {
        window.clearInterval(importProgressTimer.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      const container = containerRef.current;

      if (
        container !== null &&
        event.target instanceof Node &&
        !container.contains(event.target)
      ) {
        setOpen(false);
        setShowImportForm(false);
        setImportError(null);
        setImportSuccess(null);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        setShowImportForm(false);
        setImportError(null);
        setImportSuccess(null);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);

      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  function stopImportProgress() {
    if (importProgressTimer.current === null) {
      return;
    }

    window.clearInterval(importProgressTimer.current);
    importProgressTimer.current = null;
  }

  function startImportProgress() {
    stopImportProgress();
    setImportProgress(4);

    importProgressTimer.current = window.setInterval(() => {
      setImportProgress((current) => {
        if (current >= 92) {
          return 92;
        }

        if (current < 28) {
          return Math.min(current + 4, 28);
        }

        if (current < 65) {
          return Math.min(current + 3, 65);
        }

        return Math.min(current + 1, 92);
      });
    }, IMPORT_PROGRESS_UPDATE_MS);
  }

  function closeMenu() {
    setOpen(false);
    setShowImportForm(false);
    setImportError(null);
    setImportSuccess(null);
    onNavigate?.();
  }

  async function handleImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setImportError(null);
    setImportSuccess(null);

    if (!isAdministrator || !auth.csrfToken) {
      setImportError("Administrator authentication is required.");
      return;
    }

    const year = Number(selectedYear);
    const currentYear = new Date().getFullYear();

    if (!Number.isInteger(year) || year < 2000 || year > currentYear) {
      setImportError(`Select a season from 2000 through ${currentYear}.`);
      return;
    }

    setImporting(true);
    startImportProgress();

    try {
      const result = await importSeason(
        {
          year,
        },
        auth.csrfToken,
      );

      await queryClient.invalidateQueries({
        queryKey: leagueQueryKeys.all,
      });

      await queryClient.invalidateQueries({
        queryKey: analyticsQueryKeys.all,
      });

      await seasonsQuery.refetch();

      stopImportProgress();
      setImportProgress(100);

      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, IMPORT_COMPLETION_PAUSE_MS);
      });

      setImportSuccess(
        result.synchronization_enabled
          ? `${result.year} imported and activated.`
          : `${result.year} imported as completed history.`,
      );
      setShowImportForm(false);
      setSelectedYear("");
    } catch (currentError) {
      stopImportProgress();
      setImportProgress(0);
      setImportError(errorMessage(currentError));
    } finally {
      stopImportProgress();
      setImporting(false);
    }
  }

  return (
    <div ref={containerRef} className="season-menu">
      <button
        type="button"
        className="sidebar-league season-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="imported-season-menu"
        onClick={() => {
          setOpen((current) => !current);
        }}
      >
        <span className="season-menu-current">
          <strong>{viewedSeason?.league_name ?? "Fantasy league"}</strong>

          <small>
            {viewedSeason
              ? `${viewedSeason.year} season`
              : "No imported season"}
          </small>
        </span>

        <ChevronDown
          className={
            open
              ? "season-menu-chevron season-menu-chevron-open"
              : "season-menu-chevron"
          }
          aria-hidden="true"
        />
      </button>

      {open ? (
        <div
          id="imported-season-menu"
          className="season-menu-popover"
          role="menu"
          aria-label="Imported seasons"
        >
          <div className="season-menu-heading">
            <div>
              <strong>Imported seasons</strong>

              <span>Select league history to view</span>
            </div>

            <History aria-hidden="true" />
          </div>

          {seasonsQuery.isPending ? (
            <div className="season-menu-state" aria-live="polite">
              <RefreshCw className="spinner" aria-hidden="true" />
              Loading seasons...
            </div>
          ) : null}

          {seasonsQuery.isError ? (
            <div className="season-menu-state season-menu-error" role="alert">
              <span>Unable to load imported seasons.</span>

              <button
                type="button"
                onClick={() => {
                  void seasonsQuery.refetch();
                }}
              >
                Try again
              </button>
            </div>
          ) : null}

          {seasonsQuery.data?.seasons.map((season) => {
            const isViewed = viewedSeason?.season_id === season.season_id;

            return (
              <button
                key={season.season_id}
                type="button"
                role="menuitem"
                className={
                  isViewed
                    ? "season-menu-option season-menu-option-active"
                    : "season-menu-option"
                }
                aria-current={isViewed ? "page" : undefined}
                title={
                  season.is_active
                    ? "Synchronization-active season"
                    : `View the ${season.year} season`
                }
                onClick={() => {
                  const nextSearchParams = new URLSearchParams({
                    season_id: String(season.season_id),
                  });

                  navigate({
                    pathname: "/",
                    search: `?${nextSearchParams.toString()}`,
                  });

                  void queryClient.invalidateQueries({
                    queryKey: leagueQueryKeys.all,
                  });
                  void queryClient.invalidateQueries({
                    queryKey: analyticsQueryKeys.all,
                  });

                  closeMenu();
                }}
              >
                <span>
                  <strong>{season.league_name}</strong>

                  <small>{season.year} season</small>
                </span>

                <span
                  className={
                    season.is_active
                      ? "season-active-label"
                      : "season-history-label"
                  }
                >
                  {isViewed ? <Check aria-hidden="true" /> : null}

                  {seasonStatusLabel(season.status, season.is_active)}
                </span>
              </button>
            );
          })}

          {isAdministrator ? (
            <div className="season-import-section">
              {!showImportForm ? (
                <button
                  type="button"
                  className="season-import-trigger"
                  disabled={importing || availableYears.length === 0}
                  onClick={() => {
                    setImportError(null);
                    setImportSuccess(null);
                    setSelectedYear(
                      availableYears[0] !== undefined
                        ? String(availableYears[0])
                        : "",
                    );
                    setShowImportForm(true);
                  }}
                >
                  <Plus aria-hidden="true" />
                  Import another season
                </button>
              ) : (
                <form
                  className="season-import-form"
                  onSubmit={(event) => {
                    void handleImport(event);
                  }}
                >
                  <label>
                    <span>Season year</span>

                    <select
                      value={selectedYear}
                      disabled={importing}
                      required
                      onChange={(event) => {
                        setSelectedYear(event.target.value);
                      }}
                    >
                      {availableYears.map((year) => (
                        <option key={year} value={year}>
                          {year}
                        </option>
                      ))}
                    </select>
                  </label>

                  <p>The existing encrypted ESPN connection will be reused.</p>

                  <div className="season-import-actions">
                    <button
                      type="button"
                      disabled={importing}
                      onClick={() => {
                        setShowImportForm(false);
                        setImportError(null);
                      }}
                    >
                      Cancel
                    </button>

                    <button type="submit" disabled={importing || !selectedYear}>
                      {importing ? (
                        <>
                          <RefreshCw className="spinner" aria-hidden="true" />
                          <span>Importing {selectedYear}...</span>
                        </>
                      ) : (
                        "Import season"
                      )}
                    </button>
                  </div>

                  {importing ? (
                    <div
                      className="season-import-progress-panel"
                      aria-live="polite"
                    >
                      <div className="season-import-progress-heading">
                        <div>
                          <small>Estimated progress</small>
                          <strong>{importProgressStage.label}</strong>
                        </div>

                        <span>{importProgress}%</span>
                      </div>

                      <div
                        className="season-import-progress"
                        role="progressbar"
                        aria-label={`Importing ${selectedYear} season`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={importProgress}
                        aria-valuetext={importProgressStage.label}
                      >
                        <span
                          style={{
                            width: `${importProgress}%`,
                          }}
                        />
                      </div>

                      <p className="season-import-progress-detail">
                        {importProgressStage.detail}
                      </p>
                    </div>
                  ) : null}
                </form>
              )}

              {importError ? (
                <p
                  className="season-import-message season-import-error"
                  role="alert"
                >
                  {importError}
                </p>
              ) : null}

              {importSuccess ? (
                <p
                  className="season-import-message season-import-success"
                  aria-live="polite"
                >
                  {importSuccess}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
