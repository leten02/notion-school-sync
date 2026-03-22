import { BackendStatusCard } from "@/components/backend-status";
import { LocalSettingsStatusCard } from "@/components/local-settings-status";
import { SchedulerControlsCard } from "@/components/scheduler-controls-card";
import { UserStateCard } from "@/components/user-state-card";

export default function DashboardPage() {
  return (
    <section className="stack">
      <div className="grid-2">
        <BackendStatusCard />
        <LocalSettingsStatusCard />
        <UserStateCard />
        <SchedulerControlsCard />
      </div>
    </section>
  );
}
