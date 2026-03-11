import { useState } from "react";
import { useAuth } from "../auth/AuthContext";

export default function AuthModal() {
  const { loginWithGoogle } = useAuth();
  const [redirecting, setRedirecting] = useState(false);

  const handleLogin = () => {
    setRedirecting(true);
    loginWithGoogle();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/45 backdrop-blur-sm p-4">
      <div className="w-full max-w-xl rounded-3xl bg-white p-8 shadow-2xl">
        <div className="mb-8 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-4xl font-bold tracking-tight text-slate-800">Sign in to Job Monitor</h2>
            <p className="mt-4 text-lg text-slate-500">Use your Google account to connect your mailbox.</p>
          </div>
        </div>

        <button
          onClick={handleLogin}
          disabled={redirecting}
          className="w-full rounded-2xl border border-slate-300 bg-white px-6 py-4 text-xl font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-wait disabled:opacity-70"
        >
          <span className="inline-flex items-center gap-4">
            <span aria-hidden="true">G</span>
            {redirecting ? "Redirecting to Google..." : "Sign in with Google"}
          </span>
        </button>
      </div>
    </div>
  );
}
