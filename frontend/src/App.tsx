import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import ApplicationDetail from "./pages/ApplicationDetail";
import AccountPage from "./pages/AccountPage";
import ProgressPreview from "./pages/ProgressPreview";
import FlowPreview from "./pages/FlowPreview";
import { useAuth } from "./auth/AuthContext";
import AuthModal from "./components/AuthModal";

export default function App() {
  const { loading, user } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 text-gray-600">
        Checking authentication...
      </div>
    );
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/preview/progress" element={<ProgressPreview />} />
        <Route path="/preview/flow" element={<FlowPreview />} />
        <Route path="*" element={<AuthModal />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/preview/progress" element={<ProgressPreview />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/applications/:id" element={<ApplicationDetail />} />
      </Route>
    </Routes>
  );
}
