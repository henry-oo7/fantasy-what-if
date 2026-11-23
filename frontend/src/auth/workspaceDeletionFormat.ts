/** Pure FR-20 deletion display helpers shared by the danger zone and recovery screen. */

/** Same normalization the backend applies before comparing slugs. */
export function normalizeConfirmationSlug(value: string): string {
  return value.trim().toLowerCase();
}

/** Display a scheduled time in the viewer's locale; never raw ISO. */
export function formatDeletionDate(value: string): string {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "the scheduled time";
  }

  return date.toLocaleString(undefined, {
    dateStyle: "long",
    timeStyle: "short",
  });
}

/** Remaining time until deletion, coarse enough to stay calm. */
export function formatDeletionCountdown(
  scheduledFor: string,
  now: number,
): string | null {
  const remaining = new Date(scheduledFor).getTime() - now;

  if (!Number.isFinite(remaining) || remaining <= 0) {
    return null;
  }

  const minutes = Math.floor(remaining / 60_000);
  const days = Math.floor(minutes / (60 * 24));
  const hours = Math.floor((minutes % (60 * 24)) / 60);
  const remainingMinutes = minutes % 60;
  const plural = (count: number, unit: string) =>
    `${count} ${unit}${count === 1 ? "" : "s"}`;

  if (days > 0) {
    return `${plural(days, "day")}, ${plural(hours, "hour")} remaining`;
  }

  if (hours > 0) {
    return `${plural(hours, "hour")}, ${plural(remainingMinutes, "minute")} remaining`;
  }

  return remainingMinutes > 0
    ? `${plural(remainingMinutes, "minute")} remaining`
    : "Less than a minute remaining";
}
