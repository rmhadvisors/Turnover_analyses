import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { queryClient } from "./api";
import App from "./App";
import { SelectionProvider } from "./state";
import { ToastProvider } from "./ui";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <SelectionProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </SelectionProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
