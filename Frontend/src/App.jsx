import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./lib/AuthProvider.jsx";
import RequireAuth from "./components/RequireAuth.jsx";
import Home from "./pages/Home.jsx";
import Login from "./pages/Login.jsx";
import Account from "./pages/Account.jsx";
import Monitoring from "./pages/Monitoring.jsx";
import SocialPlatform from "./pages/SocialPlatform.jsx";

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
