import Logo from "../Logo/Logo.jsx";
import "./Footer.css";

export default function Footer() {
  return (
    <footer className="footer">
      <div className="container footer-inner">
        <Logo />
        <p>Secure, auditable public communications for government agencies.</p>
        <p className="footer-copy">© {new Date().getFullYear()} HomeFront</p>
      </div>
    </footer>
  );
}
