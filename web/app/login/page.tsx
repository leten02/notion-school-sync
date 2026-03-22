"use client";

import { useState } from "react";
import Image from "next/image";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const onGoogleSignIn = async () => {
    setError(null);
    setLoading(true);
    try {
      const supabase = createClient();
      const redirectTo = `${window.location.origin}/auth/callback`;
      const { error: signInError } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo }
      });
      if (signInError) {
        setError(signInError.message);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인 중 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="hero-shell">
      <section className="hero-content">
        <h1 className="hero-title">한 번의 입력으로 수 많은 것을 누리세요.</h1>
        <p className="hero-service">
          <span className="hero-service-notion">NOTION</span>
          <span className="hero-service-sep">-</span>
          <span className="hero-service-snippet">DAILY SNIPPET</span>
        </p>
        <p className="hero-description">
          노션에 기록한 하루를 자동으로 정리하고, 더 빠르고 쉽게 스니펫으로 연결하세요.
        </p>

        <div className="hero-actions">
          <button className="button hero-cta" onClick={onGoogleSignIn} disabled={loading}>
            {loading ? "Google 로그인 이동 중..." : "구글 로그인"}
          </button>
          {error && <p className="error">{error}</p>}
        </div>

        <div className="hero-partners">
          <div className="hero-logo-box">
            <Image
              src="/assets/notion.png"
              alt="Notion logo"
              className="hero-logo-img notion"
              width={220}
              height={72}
              priority
            />
          </div>
          <div className="hero-partner-link" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div className="hero-logo-box">
            <Image
              src="/assets/cocone.png"
              alt="Gachon Cocone School logo"
              className="hero-logo-img cocone"
              width={280}
              height={88}
              priority
            />
          </div>
        </div>
      </section>
    </main>
  );
}
