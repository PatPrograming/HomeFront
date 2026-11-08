import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./lib/AuthProvider";
import RequireAuth from "./components/RequireAuth";
import Home from "./pages/Home";
import Login from "./pages/Login";
import Account from "./pages/Account";
import Monitoring from "./pages/Monitoring";
import Agents from "./pages/Agents";
import NewAgent from "./pages/NewAgent";
import Activity from "./pages/Activity";
import Approvals from "./pages/Approvals";
import SocialPlatform from "./pages/SocialPlatform";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/social-platform" element={<SocialPlatform />} />
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
