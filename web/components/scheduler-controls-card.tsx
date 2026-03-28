"use client";

import { useState } from "react";
import { createTodayNotionPage, runSchedulerJob } from "@/lib/backend/api";

export function SchedulerControlsCard() {
  const [running, setRunning] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("버튼을 눌러 수동 실행할 수 있습니다.");

  const runJob = async (job: "notion-sync" | "weekly-report" | "monthly-report") => {
    setRunning(job);
    setMessage("실행 요청 중...");
    try {
      const result = await runSchedulerJob(job);
      if (!result.queued) {
        setMessage("실행 요청 실패");
        return;
      }
      setMessage(`${job} 작업을 큐에 등록했습니다. 5~15초 후 대시보드를 새로고침하세요.`);
    } catch (error) {
      setMessage(error instanceof Error ? `실행 실패: ${error.message}` : "실행 실패");
    } finally {
      setRunning(null);
    }
  };

  const handleCreateTodayPage = async () => {
    setRunning("create-today-page");
    setMessage("노션 페이지 생성 중...");
    try {
      const result = await createTodayNotionPage();
      if (result.status === "already_exists") {
        setMessage("오늘 페이지가 이미 존재합니다.");
      } else if (result.status === "page_created") {
        setMessage(`✅ ${result.title} 페이지가 생성됐습니다!`);
      } else {
        setMessage(`실패: ${result.status}${result.error ? ` (${result.error})` : ""}`);
      }
    } catch (error) {
      setMessage(error instanceof Error ? `실행 실패: ${error.message}` : "실행 실패");
    } finally {
      setRunning(null);
    }
  };

  return (
    <article className="card stack">
      <h2 style={{ margin: 0 }}>수동 실행</h2>
      <p className="subtitle" style={{ marginTop: -4 }}>
        스케줄 시간과 무관하게 바로 실행합니다.
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          className="button"
          disabled={Boolean(running)}
          onClick={handleCreateTodayPage}
        >
          {running === "create-today-page" ? "생성 중..." : "📄 오늘 노션 페이지 생성"}
        </button>
        <button
          className="button secondary"
          disabled={Boolean(running)}
          onClick={() => runJob("notion-sync")}
        >
          {running === "notion-sync" ? "실행 중..." : "일간 동기화 실행"}
        </button>
        <button
          className="button secondary"
          disabled={Boolean(running)}
          onClick={() => runJob("weekly-report")}
        >
          {running === "weekly-report" ? "실행 중..." : "주간 리포트 실행"}
        </button>
        <button
          className="button secondary"
          disabled={Boolean(running)}
          onClick={() => runJob("monthly-report")}
        >
          {running === "monthly-report" ? "실행 중..." : "월간 리포트 실행"}
        </button>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        {message}
      </p>
    </article>
  );
}
