import { Link } from "react-router-dom";
import { useAuth } from "../../lib/AuthProvider";
import PlatformIcon from "../PlatformIcons/PlatformIcons";
import "./Hero.css";

interface HeroProps {
  mission: string[];
  buttons?: string[];
}

export default function Hero({ mission, buttons }: HeroProps) {
  const { session } = useAuth();
  const primaryLabel = buttons?.[0] || "Start a communication";
  const secondaryLabel = buttons?.[1] || "View agents";
  return (
    <section className="hero">
      <div className="container hero-grid">
        <div className="hero-text">
          <p className="eyebrow">For government communicators</p>
          <h1 className="mission">
            <span className="line-1">{mission[0]}</span>{" "}
            <span className="line-2">{mission[1]}</span>
          </h1>
          <p className="hero-lede">
            Give agents the essentials: what happened, which links matter, and any supporting
            visuals. HomeFront handles the outreach across the channels your agency already uses.
          </p>
          <div className="hero-buttons">
            <Link className="btn btn-primary" to="/social-platform">
              {primaryLabel}
              <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                <path d="M3 8h10M9 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Link>
            {session ? (
              <Link className="btn btn-outline" to="/agents">
                {secondaryLabel}
              </Link>
            ) : (
              <Link className="btn btn-outline" to="/login">
                Sign in
              </Link>
            )}
          </div>
        </div>

        <div className="demo-box" aria-hidden="true">
          <div className="demo-bar">
            <span />
            <span />
            <span />
          </div>
          <div className="demo-body">
            <div className="demo-chips">
              {(["x", "bluesky", "reddit"] as const).map((p) => (
                <span key={p} className={`demo-chip demo-chip-${p}`}>
                  <PlatformIcon name={p} size={16} />
                </span>
              ))}
              <span className="demo-chip-label">3 channels selected</span>
            </div>
            <div className="demo-line demo-line-title" />
            <div className="demo-line" />
            <div className="demo-line" />
            <div className="demo-line demo-line-short" />
            <div className="demo-link">
              <svg viewBox="0 0 16 16" width="14" height="14">
                <path d="M6.5 9.5a3 3 0 0 0 4.2 0l2-2a3 3 0 0 0-4.2-4.2l-.8.8M9.5 6.5a3 3 0 0 0-4.2 0l-2 2a3 3 0 0 0 4.2 4.2l.8-.8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
              <span />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
