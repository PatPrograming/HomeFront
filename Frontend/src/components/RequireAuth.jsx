import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../lib/AuthProvider.jsx";

// Wrap any route that needs a signed-in user.
export default function RequireAuth({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <p style={{ padding: 24 }}>Loading…</p>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}
