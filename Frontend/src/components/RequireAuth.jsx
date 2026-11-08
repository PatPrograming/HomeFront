import { Link, Navigate, useLocation } from "react-router-dom";
import Layout from "./Layout.jsx";
import { useAuth } from "../lib/AuthProvider.jsx";
import { gate } from "../lib/roles.js";

// Wrap any route that needs a signed-in staffer. With role="admin", only the Cognito group
// "admin" gets in. This only hides screens: the API checks the same group on every admin call.
export default function RequireAuth({ role, children }) {
  const { session } = useAuth();
  const location = useLocation();
  const verdict = gate(session, role);

  if (verdict === "login") return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (verdict === "forbidden") {
    return (
      <Layout>
        <section className="gate-message" role="alert">
          <h1>Not allowed</h1>
          <p>
            {role === "admin"
              ? "This page is for the Chief of Staff (Cognito group admin)."
              : "Your account is not in the staffer or admin group."}
          </p>
          <Link to="/console">Back to the console</Link>
        </section>
      </Layout>
    );
  }
  return children;
}
