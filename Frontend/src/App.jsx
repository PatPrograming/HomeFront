import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider } from "./lib/AuthProvider.jsx";
import RequireAuth from "./components/RequireAuth.jsx";
import Home from "./pages/Home.jsx";
import Login from "./pages/Login.jsx";
import AuthCallback from "./pages/AuthCallback.jsx";
import Account from "./pages/Account.jsx";
import Console from "./pages/Console.jsx";
import Admin from "./pages/Admin.jsx";

// Cognito sends the browser back to the site root with ?code=…&state=… (or ?error=…), because the
// staff-web client's callback URL is the root. Finish the sign-in there; otherwise show the home page.
function HomeOrCallback() {
  const params = new URLSearchParams(useLocation().search);
  return params.has("code") || params.has("error") ? <AuthCallback /> : <Home />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomeOrCallback />} />
          <Route path="/login" element={<Login />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route
            path="/console"
            element={
              <RequireAuth>
                <Console />
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAuth role="admin">
                <Admin />
              </RequireAuth>
            }
          />
          <Route
            path="/account"
            element={
              <RequireAuth role="any">
                <Account />
              </RequireAuth>
            }
          />
          {/* The old in-memory gateway demo lived here; its API routes are gone, so send old links to the console. */}
          <Route path="/monitoring" element={<Navigate to="/console" replace />} />
          <Route path="*" element={<Home />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
