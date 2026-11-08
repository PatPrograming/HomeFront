import { useState } from "react";
import { NavLink, Link } from "react-router-dom";
import Logo from "../Logo/Logo";
import { ADMIN_LINK, NAV_LINKS } from "../../config";
import { useAuth } from "../../lib/AuthProvider";
import { isAdmin } from "../../lib/roles";
import "./Navbar.css";

export default function Navbar() {
  const { session } = useAuth();
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  const links = isAdmin(session) ? [...NAV_LINKS, ADMIN_LINK] : NAV_LINKS;

  return (
    <header className="navbar">
      <div className="container navbar-inner">
        <Logo />

        <button
          type="button"
          className="hamburger"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          aria-controls="main-nav"
          onClick={() => setOpen((v) => !v)}
        >
          <span />
          <span />
          <span />
        </button>

        <nav id="main-nav" className={`nav-links${open ? " is-open" : ""}`} aria-label="Main">
          {links.map((l) => (
            <NavLink key={l.href} to={l.href} end={l.href === "/"} onClick={close}>
              {l.label}
            </NavLink>
          ))}
          {!session && (
            <Link to="/login" className="nav-cta" onClick={close}>
              Sign in
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
