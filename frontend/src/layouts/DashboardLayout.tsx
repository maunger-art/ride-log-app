import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

const tabs = [
  { label: "Overview", to: "#" },
  { label: "Training", to: "#" },
  { label: "Recovery", to: "#" },
];

const DashboardLayout = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const isCoachRoute = location.pathname.startsWith("/coach");
  const toggleLabel = isCoachRoute ? "Switch to Patient" : "Switch to Coach";

  const handleToggle = () => {
    navigate(isCoachRoute ? "/patient" : "/coach");
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar__brand">Ride Log</div>
        <nav className="sidebar__nav">
          <NavLink to="/coach" className={({ isActive }) => (isActive ? "active" : "")}
            end>
            Coach Dashboard
          </NavLink>
          <NavLink to="/patient" className={({ isActive }) => (isActive ? "active" : "")}>
            Patient Dashboard
          </NavLink>
        </nav>
        <div className="sidebar__footer">
          <span className="pill">Demo</span>
          <p>Shared layout with Vite + React</p>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar__tabs">
            {tabs.map((tab) => (
              <button key={tab.label} className="tab">
                {tab.label}
              </button>
            ))}
          </div>
          <button className="toggle" onClick={handleToggle}>
            {toggleLabel}
          </button>
        </header>
        <section className="content">
          <Outlet />
        </section>
      </main>
    </div>
  );
};

export default DashboardLayout;
