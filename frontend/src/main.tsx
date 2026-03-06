import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles/index.css";
import { AuthProvider } from "./auth/AuthContext";
import { JourneyProvider } from "./journey/JourneyContext";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <JourneyProvider>
          <App />
        </JourneyProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
