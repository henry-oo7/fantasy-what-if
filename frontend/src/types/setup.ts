export type InstallationStatus =
  | "setup_required"
  | "setup_in_progress"
  | "active";

export interface SetupStatus {
  status: InstallationStatus;
  installation_exists: boolean;
  bootstrap_available: boolean;
}
