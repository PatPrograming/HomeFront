import Logo from "../Logo/Logo.jsx";
import { LOGO_SRC, NAV_LINKS } from "../../config.js";
import "./Navbar.css";

export default function Navbar() {
  return (
    <>
      <header className="navbar">
        <Logo src={LOGO_SRC} />
        <nav className="nav-links" aria-label="Main">
          {NAV_LINKS.map((l) => (
            <a key={l.href} href={l.href}>
              {l.label}
            </a>
          ))}
          <button className="hamburger" aria-label="Menu">
            <span />
            <span />
            <span />
          </button>
        </nav>
      </header>
      <div className="accent-bar" />
    </>
  );
}
