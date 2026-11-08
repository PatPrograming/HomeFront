import { Link } from "react-router-dom";
import logoImg from "../../assets/HomeFront_Logo.png";
import "./Logo.css";

// Envelope logo image + HomeFront wordmark.
export default function Logo() {
  return (
    <Link className="logo" to="/" aria-label="HomeFront home">
      <img className="logo-mark" src={logoImg} alt="" />
      <span className="logo-word">
        Home<span>Front</span>
      </span>
    </Link>
  );
}
