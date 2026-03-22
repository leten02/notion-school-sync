"use client";

import { FormEvent, useEffect, useState } from "react";
import { getMySettings, saveMySettings, syncCurrentUser } from "@/lib/backend/api";

type FormState = {
  notionToken: string;
  notionPageId: string;
  schoolApiKey: string;
  geminiApiKey: string;
};

const EMPTY_FORM: FormState = {
  notionToken: "",
  notionPageId: "",
  schoolApiKey: "",
  geminiApiKey: ""
};

export default function SettingsPage() {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string>("로딩 중...");
  const [hasNotionToken, setHasNotionToken] = useState(false);
  const [hasSchoolApiKey, setHasSchoolApiKey] = useState(false);
  const [hasGeminiApiKey, setHasGeminiApiKey] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        await syncCurrentUser();
        const saved = await getMySettings();
        setForm((prev) => ({
          ...prev,
          notionPageId: saved.notion_page_id ?? ""
        }));
        setSavedAt(saved.updated_at);
        setHasNotionToken(saved.has_notion_token);
        setHasSchoolApiKey(saved.has_school_api_key);
        setHasGeminiApiKey(saved.has_gemini_api_key);
        setSavedMessage("서버 저장 상태를 불러왔습니다.");
      } catch (error) {
        setSavedMessage(
          error instanceof Error ? `불러오기 실패: ${error.message}` : "불러오기 실패"
        );
      }
    };
    load();
  }, []);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSavedMessage("저장 중...");

    const payload: {
      notion_page_id: string | null;
      notion_token?: string;
      school_api_key?: string;
      gemini_api_key?: string;
    } = {
      notion_page_id: form.notionPageId.trim() || null
    };

    if (form.notionToken.trim()) {
      payload.notion_token = form.notionToken.trim();
    }
    if (form.schoolApiKey.trim()) {
      payload.school_api_key = form.schoolApiKey.trim();
    }
    if (form.geminiApiKey.trim()) {
      payload.gemini_api_key = form.geminiApiKey.trim();
    }

    try {
      const saved = await saveMySettings(payload);
      setSavedAt(saved.updated_at);
      setHasNotionToken(saved.has_notion_token);
      setHasSchoolApiKey(saved.has_school_api_key);
      setHasGeminiApiKey(saved.has_gemini_api_key);
      setSavedMessage("서버에 암호화 저장했습니다.");
      setForm((prev) => ({
        ...prev,
        notionToken: "",
        schoolApiKey: "",
        geminiApiKey: ""
      }));
    } catch (error) {
      setSavedMessage(error instanceof Error ? `저장 실패: ${error.message}` : "저장 실패");
    }
  };

  const onChange = (key: keyof FormState, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <section className="stack">
      <article className="card">
        <h1 className="title" style={{ marginBottom: 10 }}>
          API 키 입력
        </h1>
        <p className="subtitle">
          Notion/1000.school/Gemini 연동 키를 서버 DB에 암호화 저장합니다.
        </p>
      </article>

      <form className="card stack" onSubmit={onSubmit}>
        <label className="stack">
          <span className="label">Notion 토큰</span>
          <input
            className="input"
            type="password"
            value={form.notionToken}
            onChange={(event) => onChange("notionToken", event.target.value)}
            placeholder="secret_..."
            autoComplete="off"
          />
          <span className="field-help">
            발급:{" "}
            <a href="https://www.notion.so/my-integrations" target="_blank" rel="noreferrer">
              notion.so/my-integrations
            </a>
          </span>
          <span className="muted">
            현재 저장 상태: {hasNotionToken ? "저장됨" : "없음"} (빈칸 저장 시 기존 값 유지)
          </span>
        </label>

        <label className="stack">
          <span className="label">Notion 부모 페이지 ID</span>
          <input
            className="input"
            value={form.notionPageId}
            onChange={(event) => onChange("notionPageId", event.target.value)}
            placeholder="32자리 페이지 ID"
            autoComplete="off"
          />
        </label>

        <label className="stack">
          <span className="label">1000.school API 키</span>
          <input
            className="input"
            type="password"
            value={form.schoolApiKey}
            onChange={(event) => onChange("schoolApiKey", event.target.value)}
            placeholder="I_wrdo_..."
            autoComplete="off"
          />
          <span className="field-help">
            발급:{" "}
            <a
              href="https://app.1000.school/settings?menu=api"
              target="_blank"
              rel="noreferrer"
            >
              app.1000.school/settings?menu=api
            </a>
          </span>
          <span className="muted">
            현재 저장 상태: {hasSchoolApiKey ? "저장됨" : "없음"} (빈칸 저장 시 기존 값 유지)
          </span>
        </label>

        <label className="stack">
          <span className="label">Gemini API 키</span>
          <input
            className="input"
            type="password"
            value={form.geminiApiKey}
            onChange={(event) => onChange("geminiApiKey", event.target.value)}
            placeholder="AIzaSy..."
            autoComplete="off"
          />
          <span className="field-help">
            발급:{" "}
            <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer">
              aistudio.google.com/app/apikey
            </a>
          </span>
          <span className="muted">
            현재 저장 상태: {hasGeminiApiKey ? "저장됨" : "없음"} (빈칸 저장 시 기존 값 유지)
          </span>
        </label>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="submit" className="button">
            암호화 저장
          </button>
          <span className="muted">{savedMessage}</span>
          {savedAt && <span className="badge">최근 저장: {new Date(savedAt).toLocaleString()}</span>}
        </div>
      </form>

      <article className="card">
        <p className="muted" style={{ margin: 0 }}>
          API 키는 브라우저가 아닌 백엔드에서 암호화(`Fernet`) 후 저장됩니다.
        </p>
      </article>
    </section>
  );
}
