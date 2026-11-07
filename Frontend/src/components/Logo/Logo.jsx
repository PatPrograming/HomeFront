import "./Logo.css";

// The logo image already contains the GuardRail wordmark.
export default function Logo({ src, alt = "GuardRail Solutions" }) {
  return (
    <a className="logo" href="/">
      {src && <img className="logo-img" src={src} alt={alt} />}
    </a>
  );
}
