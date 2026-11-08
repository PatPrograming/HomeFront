import type { ReactNode } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import Layout from "./Layout";
import { useAuth } from "../lib/AuthProvider";
import { gate, type Role } from "../lib/roles";

// Wrap any route that needs a signed-in staffer. With role="admin", only the Cognito group
// "admin" gets in. This only hides screens: the API checks the same group on every admin call.
export default function RequireAuth({ role, children }: { role?: Role; children: ReactNode }) {
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
          <Link to={role === "admin" ? "/console" : "/account"}>
            {role === "admin" ? "Back to the console" : "Your account and sign out"}
          </Link>
        </section>
      </Layout>
    );
  }
  return <>{children}</>;
}
