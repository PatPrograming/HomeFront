import { useState } from "react";
import { Link } from "react-router-dom";
import Logo from "../Logo/Logo.jsx";
import { ADMIN_LINK, LOGO_SRC, NAV_LINKS } from "../../config.js";
import { useAuth } from "../../lib/AuthProvider.jsx";
import { isAdmin } from "../../lib/roles.js";
import "./Navbar.css";

export default function Navbar() {
  const { session } = useAuth();
  const [open, setOpen] = useState(false);
  const links = isAdmin(session) ? [...NAV_LINKS, ADMIN_LINK] : NAV_LINKS;

  return (
    <>
      <header className="navbar">
        <Logo src={LOGO_SRC} />
        <button
          type="button"
          className="hamburger"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          aria-controls="main-nav"
          onClick={() => setOpen(!open)}
        >
          <span />
          <span />
          <span />
        </button>
        <nav id="main-nav" className={`nav-links ${open ? "open" : ""}`} aria-label="Main">
          {links.map((l) => (
            <Link key={l.href} to={l.href} onClick={() => setOpen(false)}>
              {l.label}
            </Link>
          ))}
          {session ? (
            <Link to="/account" onClick={() => setOpen(false)}>
              {session.username}
            </Link>
          ) : (
            <Link to="/login" onClick={() => setOpen(false)}>
              Sign in
            </Link>
          )}
        </nav>
      </header>
      <div className="accent-bar" />
    </>
  );
}
