import { useState } from "react";
import { NavLink, Link } from "react-router-dom";
import Logo from "../Logo/Logo.jsx";
import { NAV_LINKS } from "../../config.js";
import { useAuth } from "../../lib/AuthProvider.jsx";
import "./Navbar.css";

export default function Navbar() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  return (
    <header className="navbar">
      <div className="container navbar-inner">
        <Logo />

        <button
          className="hamburger"
          aria-label="Menu"
          aria-expanded={open}
          aria-controls="main-nav"
          onClick={() => setOpen((v) => !v)}
        >
          <span />
          <span />
          <span />
        </button>

        <nav id="main-nav" className={`nav-links${open ? " is-open" : ""}`} aria-label="Main">
          {NAV_LINKS.map((l) => (
            <NavLink key={l.href} to={l.href} end={l.href === "/"} onClick={close}>
              {l.label}
            </NavLink>
          ))}
          <Link
            to={user ? "/account" : "/login"}
            className="nav-cta"
            onClick={close}
          >
            {user ? "Account" : "Sign in"}
          </Link>
        </nav>
      </div>
    </header>
  );
}
