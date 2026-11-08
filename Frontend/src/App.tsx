import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { AuthProvider } from "./lib/AuthProvider";
import RequireAuth from "./components/RequireAuth";
import Home from "./pages/Home";
import Login from "./pages/Login";
import Account from "./pages/Account";
import Monitoring from "./pages/Monitoring";
import Emails from "./pages/Emails";
import Agents from "./pages/Agents";
import NewAgent from "./pages/NewAgent";
import Activity from "./pages/Activity";
import Approvals from "./pages/Approvals";
import SocialPlatform from "./pages/SocialPlatform";

// /agents/<id>/email: open that agent's Email setup on My agents.
function AgentEmailRedirect() {
  const { agentId = "" } = useParams();
  return <Navigate replace to={`/agents?edit=${encodeURIComponent(agentId)}&section=email`} />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
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
            path="/account"
            element={
              <RequireAuth>
                <Account />
              </RequireAuth>
            }
          />
          <Route
            path="/agents"
            element={
              <RequireAuth>
                <Agents />
              </RequireAuth>
            }
          />
          <Route
            path="/agents/new"
            element={
              <RequireAuth>
                <NewAgent />
              </RequireAuth>
            }
          />
          <Route
            path="/agents/:agentId/email"
            element={
              <RequireAuth>
                <AgentEmailRedirect />
              </RequireAuth>
            }
          />
          <Route
            path="/approvals"
            element={
              <RequireAuth>
                <Approvals />
              </RequireAuth>
            }
          />
          <Route
            path="/activity"
            element={
              <RequireAuth>
                <Activity />
              </RequireAuth>
            }
          />
          <Route
            path="/monitoring"
            element={
              <RequireAuth>
                <Monitoring />
              </RequireAuth>
            }
          />
          <Route path="*" element={<Home />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
