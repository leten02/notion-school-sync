import Link from "next/link";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { LogoutButton } from "@/components/logout-button";

export default async function ProtectedLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  const supabase = createClient();
  const {
    data: { user }
  } = await supabase.auth.getUser();

  if (!user) {
    redirect("/login");
  }

  return (
    <main className="app-shell">
      <div className="container">
        <header className="card app-header">
          <div className="app-brand-wrap">
            <strong className="app-brand">
              <span className="app-brand-notion">NOTION</span>
              <span className="app-brand-sep">-</span>
              <span className="app-brand-snippet">DAILY SNIPPET</span>
            </strong>
            <span className="muted">{user.email}</span>
          </div>

          <div className="app-nav">
            <Link className="button secondary" href="/settings">
              API 키 입력
            </Link>
            <LogoutButton />
          </div>
        </header>
        {children}
      </div>
    </main>
  );
}
