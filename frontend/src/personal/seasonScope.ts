import { useParams } from "react-router-dom";

/**
 * The personal Season in the route, or undefined on a legacy workspace route.
 * The id is only a locator: the backend authorizes every read.
 */
export function useSeasonScope(): { seasonId: number | undefined; basePath: string } {
  const { seasonId: parameter } = useParams();
  const seasonId =
    parameter !== undefined && /^[1-9][0-9]{0,9}$/.test(parameter)
      ? Number(parameter)
      : undefined;

  return {
    seasonId,
    basePath: seasonId === undefined ? "" : `/account/seasons/${seasonId}`,
  };
}
