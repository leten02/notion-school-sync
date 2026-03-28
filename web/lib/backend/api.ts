"use client";

import { createClient } from "@/lib/supabase/client";

export type MySettingsResponse = {
  user_id: string;
  notion_page_id: string | null;
  has_notion_token: boolean;
  has_school_api_key: boolean;
  has_gemini_api_key: boolean;
  updated_at: string | null;
};

export type SaveSettingsInput = {
  notion_page_id?: string | null;
  notion_token?: string | null;
  school_api_key?: string | null;
  gemini_api_key?: string | null;
};

export type MyDashboardResponse = {
  settings: MySettingsResponse;
  state: {
    user_id: string;
    last_notion_check_at: string | null;
    last_notion_sync_at: string | null;
    last_weekly_report_at: string | null;
    last_monthly_report_at: string | null;
    last_status: string | null;
    last_error: string | null;
    updated_at: string | null;
  } | null;
};

function getBackendBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_BACKEND_API_URL;
  if (!base) {
    throw new Error("NEXT_PUBLIC_BACKEND_API_URL is missing.");
  }
  return base.replace(/\/$/, "");
}

async function getAccessToken() {
  const supabase = createClient();
  const {
    data: { session }
  } = await supabase.auth.getSession();
  const accessToken = session?.access_token;
  if (!accessToken) {
    throw new Error("Supabase session token not found.");
  }
  return accessToken;
}

async function authedRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const base = getBackendBaseUrl();
  const token = await getAccessToken();
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
      Authorization: `Bearer ${token}`
    },
    cache: "no-store"
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`API ${response.status}: ${text}`);
  }
  return (await response.json()) as T;
}

export async function syncCurrentUser() {
  return authedRequest("/users/me/sync", { method: "POST" });
}

export async function getMySettings() {
  return authedRequest<MySettingsResponse>("/settings/me");
}

export async function saveMySettings(payload: SaveSettingsInput) {
  return authedRequest<MySettingsResponse>("/settings/me", {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function getMyDashboard() {
  return authedRequest<MyDashboardResponse>("/dashboard/me");
}

type SchedulerRunType = "notion-sync" | "weekly-report" | "monthly-report";

export async function runSchedulerJob(type: SchedulerRunType) {
  return authedRequest<{ queued: boolean }>(`/scheduler/run/${type}`, {
    method: "POST"
  });
}

export async function createTodayNotionPage() {
  return authedRequest<{ status: string; title?: string; page_id?: string; error?: string }>(
    "/dashboard/me/create-today-page",
    { method: "POST" }
  );
}
