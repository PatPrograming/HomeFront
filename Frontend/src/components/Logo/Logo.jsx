import { Link } from "react-router-dom";
import "./Logo.css";

// Envelope mark + HomeFront wordmark, drawn in SVG so it stays crisp on any background.
export default function Logo() {
  return (
    <Link className="logo" to="/" aria-label="HomeFront home">
      <svg className="logo-mark" viewBox="0 0 40 40" aria-hidden="true">
        <rect width="40" height="40" rx="10" fill="var(--blue-800)" />
        <rect x="7" y="11" width="26" height="18" rx="3" fill="#fff" />
        <path d="M7 14.5 20 23l13-8.5" fill="none" stroke="var(--blue-600)" strokeWidth="2.2" strokeLinejoin="round" />
        <path d="M7 26.5 15 20M33 26.5 25 20" stroke="var(--red-600)" strokeWidth="2.2" strokeLinecap="round" />
      </svg>
      <span className="logo-word">
        Home<span>Front</span>
      </span>
    </Link>
  );
}
