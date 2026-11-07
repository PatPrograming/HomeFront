import Navbar from "./Navbar/Navbar.jsx";
import Footer from "./Footer/Footer.jsx";

// Shared shell for every page: navbar on top, footer on bottom.
export default function Layout({ children }) {
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <Navbar />
      <main style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        {children}
      </main>
      <Footer />
    </div>
  );
}
