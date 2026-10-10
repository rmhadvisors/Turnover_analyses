/** Navigation and the sidebar: the one place to pick the client and the viewing year. */

import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { clearAllCaches, useWorkspace, type Row } from "./api";
import { DISPLAY_MODES, currentFy, fyChoices } from "./format";
import AlertsPage from "./pages/Alerts";
import ClientsPage from "./pages/Clients";
import DashboardPage from "./pages/Dashboard";
import DataPage from "./pages/Data";
import SettingsPage from "./pages/Settings";
import { useSelection } from "./state";
import { Button, ErrorNotice, Select, TextInput } from "./ui";

const PAGES: [string, string, string][] = [
  ["/", "Dashboard", "▦"],
  ["/clients", "Clients", "👥"],
  ["/data", "Data", "⇪"],
  ["/alerts", "Alerts", "🔔"],
  ["/settings", "Settings", "⚙"],
];

function Sidebar() {
  const sel = useSelection();
  const workspace = useWorkspace();
  const clients: Row[] = workspace.data?.clients ?? [];
  const dataFys: string[] = workspace.data?.fys ?? [];
  const alertCount: number = workspace.data?.unacknowledged_alerts ?? 0;
  const fyOptions = fyChoices(dataFys);
  const withData = new Set(dataFys);

  // Drop a remembered client that no longer exists, and default the year once data is known.
  useEffect(() => {
    if (!workspace.data) return;
    if (sel.clientId !== null && !clients.some((c) => c.id === sel.clientId)) sel.setClientId(null);
    if (!sel.fy || !fyOptions.includes(sel.fy)) sel.setFy(dataFys[0] ?? currentFy());
  }, [workspace.data]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <h4>Workspace</h4>
      <ErrorNotice error={workspace.error} />
      <Select
        label="Client"
        value={sel.clientId}
        options={[[null, "All clients"], ...clients.map((c): [number, string] => [c.id, c.name])]}
        onChange={sel.setClientId}
      />
      <Select
        label="Viewing year"
        help="The year the dashboard, reports, alerts and manual entry show. Imports detect their own year from the files."
        value={sel.fy}
        options={fyOptions.map((fy): [string, string] => [fy, withData.has(fy) ? `FY ${fy}` : `FY ${fy} · no data yet`])}
        onChange={sel.setFy}
      />
      <h4>Display</h4>
      <Select
        label="Amount display"
        value={sel.displayMode}
        options={Object.keys(DISPLAY_MODES).map((m): [string, string] => [m, m])}
        onChange={sel.setDisplayMode}
      />
      <TextInput label="Reviewer name" value={sel.reviewer} onChange={sel.setReviewer} placeholder="Used when acknowledging alerts" />
      <Link to="/alerts" className="alert-link" title="Critical and warning alerts not yet acknowledged, across every client and year.">
        🔔 {alertCount} unacknowledged (all clients)
      </Link>
      <div>
        <Button title="Reload everything from the backend." onClick={() => clearAllCaches()}>
          ⟳ Refresh data
        </Button>
      </div>
    </>
  );
}

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.pathname]);

  return (
    <div className="app">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand">📈 Turnover Analysis</div>
        <nav className="nav">
          {PAGES.map(([path, title, icon]) => (
            <NavLink key={path} to={path} end={path === "/"}>
              {icon} {title}
            </NavLink>
          ))}
        </nav>
        <Sidebar />
      </aside>
      {menuOpen && <div className="sidebar-backdrop" onClick={() => setMenuOpen(false)} />}
      <main className="main">
        <span className="menu-btn">
          <Button onClick={() => setMenuOpen(true)}>☰ Menu</Button>
        </span>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/clients" element={<ClientsPage />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<DashboardPage />} />
        </Routes>
      </main>
    </div>
  );
}
