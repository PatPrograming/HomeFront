import { Link } from "react-router-dom";
import Logo from "../Logo/Logo.jsx";
import { LOGO_SRC, NAV_LINKS } from "../../config.js";
import { useAuth } from "../../lib/AuthProvider.jsx";
import "./Navbar.css";

export default function Navbar() {
  const { user } = useAuth();

  return (
    <>
      <header className="navbar">
        <Logo src={LOGO_SRC} />
        <nav className="nav-links" aria-label="Main">
          {NAV_LINKS.map((l) => (
            <Link key={l.href} to={l.href}>
              {l.label}
            </Link>
          ))}
          {user ? (
            <Link to="/account">Account</Link>
          ) : (
            <Link to="/login">Sign in</Link>
          )}
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
