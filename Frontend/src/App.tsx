import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { AuthProvider } from "./lib/AuthProvider";
import RequireAuth from "./components/RequireAuth";
import Home from "./pages/Home";
import Login from "./pages/Login";
import AuthCallback from "./pages/AuthCallback";
import Account from "./pages/Account";
import Console from "./pages/Console";
import Admin from "./pages/Admin";
import People from "./pages/People";
import Emails from "./pages/Emails";
import SocialPlatform from "./pages/SocialPlatform";
import Agents from "./pages/Agents";
import NewAgent from "./pages/NewAgent";
import Approvals from "./pages/Approvals";
import Activity from "./pages/Activity";
import Monitoring from "./pages/Monitoring";
import Reports from "./pages/Reports";

// Cognito sends the browser back to the site root with ?code=…&state=… (or ?error=…), because the
// staff-web client's callback URL is the root. Finish the sign-in there; otherwise show the home page.
function HomeOrCallback() {
  const params = new URLSearchParams(useLocation().search);
  return params.has("code") || params.has("error") ? <AuthCallback /> : <Home />;
}

// /agents/<id>/email: open that agent's Email setup on the Agents tab.
function AgentEmailRedirect() {
  const { agentId = "" } = useParams();
  return <Navigate replace to={`/agents?edit=${encodeURIComponent(agentId)}&section=email`} />;
}

// The teammates' agent tabs. Any signed-in staffer can open them; the API lets staffers read and
// only the admin group change anything (agents, grants, email setup, approvals, runs).
const TEAM_TABS = [
  { path: "/agents", element: <Agents /> },
  { path: "/agents/new", element: <NewAgent /> },
  { path: "/agents/:agentId/email", element: <AgentEmailRedirect /> },
  { path: "/approvals", element: <Approvals /> },
  { path: "/reports", element: <Reports /> },
  { path: "/activity", element: <Activity /> },
  { path: "/monitoring", element: <Monitoring /> },
];

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomeOrCallback />} />
          <Route path="/login" element={<Login />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/emails" element={<Emails />} />
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
            path="/admin/people"
            element={
              <RequireAuth role="admin">
                <People />
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
          {TEAM_TABS.map(({ path, element }) => (
            <Route key={path} path={path} element={<RequireAuth>{element}</RequireAuth>} />
          ))}
          <Route path="*" element={<Home />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
