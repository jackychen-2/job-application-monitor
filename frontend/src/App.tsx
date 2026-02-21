import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import ApplicationDetail from "./pages/ApplicationDetail";
import EvalDashboard from "./pages/eval/EvalDashboard";
import ReviewQueue from "./pages/eval/ReviewQueue";
import ReviewEmail from "./pages/eval/ReviewEmail";
import EvalRuns from "./pages/eval/EvalRuns";
import RunDetail from "./pages/eval/RunDetail";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/applications/:id" element={<ApplicationDetail />} />
        <Route path="/eval" element={<EvalDashboard />} />
        <Route path="/eval/review" element={<ReviewQueue />} />
        <Route path="/eval/review/:id" element={<ReviewEmail />} />
        <Route path="/eval/runs" element={<EvalRuns />} />
        <Route path="/eval/runs/:id" element={<RunDetail />} />
      </Route>
    </Routes>
  );
}
