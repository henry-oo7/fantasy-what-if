export const MINIMUM_SEASON_YEAR = 2000;
const LEAGUE_ID_PATTERN = /^[0-9]{1,15}$/;

export interface ProviderTargetErrors {
  leagueId?: string;
  seasonYear?: string;
}

/** Client-side ESPN League ID and season-year checks; the server re-validates. */
export function validateProviderTarget(
  leagueId: string,
  seasonYear: string,
): ProviderTargetErrors {
  const errors: ProviderTargetErrors = {};
  const currentYear = new Date().getFullYear();
  const trimmedId = leagueId.trim();

  if (!LEAGUE_ID_PATTERN.test(trimmedId) || /^0+$/.test(trimmedId)) {
    errors.leagueId = "Enter the numeric League ID from your ESPN league address.";
  }

  const year = Number(seasonYear.trim());

  if (
    !/^[0-9]{4}$/.test(seasonYear.trim()) ||
    year < MINIMUM_SEASON_YEAR ||
    year > currentYear
  ) {
    errors.seasonYear = `Enter a season year from ${MINIMUM_SEASON_YEAR} to ${currentYear}.`;
  }

  return errors;
}
