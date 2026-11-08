import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider } from "./lib/AuthProvider";
import RequireAuth from "./components/RequireAuth";
import Home from "./pages/Home";
import Login from "./pages/Login";
import AuthCallback from "./pages/AuthCallback";
import Account from "./pages/Account";
import Console from "./pages/Console";
import Admin from "./pages/Admin";
import SocialPlatform from "./pages/SocialPlatform";

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
            path="/social-platform"
            element={
              <RequireAuth>
                <SocialPlatform />
              </RequireAuth>
            }
          />
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
