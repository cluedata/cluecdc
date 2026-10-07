"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertCircle,
  Bell,
  Box,
  ChevronRight,
  Database,
  FileClock,
  FileJson,
  GitBranch,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Radio,
  Settings,
  ShieldCheck,
  Sun,
  Table2,
  Workflow,
  Users,
  Send,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { AlertSummary } from "@cluecdc/contracts";
import { api, ApiError, relativeTime } from "@/lib/api";
type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  adminOnly?: boolean;
};
const groups: { id: string; label: string; items: NavItem[] }[] = [
  {
    id: "overview",
    label: "Overview",
    items: [{ label: "Overview", href: "/overview", icon: LayoutDashboard }],
  },
  {
    id: "data-flow",
    label: "Data Flow",
    items: [
      { label: "Pipelines", href: "/pipelines", icon: GitBranch },
      { label: "Deliveries", href: "/deliveries", icon: Send },
    ],
  },
  {
    id: "connections",
    label: "Connections",
    items: [
      { label: "Sources", href: "/sources", icon: Database },
      {
        label: "Destinations",
        href: "/destinations",
        icon: Workflow,
      },
    ],
  },
  {
    id: "infrastructure",
    label: "Infrastructure",
    items: [
      { label: "Kafka Clusters", href: "/kafka/clusters", icon: Radio },
      { label: "Topics", href: "/kafka/topics", icon: Table2 },
      {
        label: "Consumer Groups",
        href: "/kafka/consumer-groups",
        icon: FileJson,
      },
      { label: "Connect Clusters", href: "/connect/clusters", icon: Box },
    ],
  },
  {
    id: "operations",
    label: "Operations",
    items: [
      { label: "Monitoring", href: "/monitoring", icon: Activity },
      { label: "Alerts", href: "/alerts", icon: Bell },
      {
        label: "Error Center",
        href: "/operations/errors",
        icon: AlertCircle,
      },
      {
        label: "Audit Trail",
        href: "/operations/audit",
        icon: FileClock,
      },
    ],
  },
  {
    id: "system",
    label: "System",
    items: [
      { label: "Settings", href: "/settings", icon: Settings },
      { label: "Users", href: "/settings/users", icon: Users, adminOnly: true },
      {
        label: "Security",
        href: "/settings/security",
        icon: ShieldCheck,
        adminOnly: true,
      },
    ],
  },
];
function SidebarItem({
  item,
  path,
  onNavigate,
}: {
  item: NavItem;
  path: string;
  onNavigate: () => void;
}) {
  const active =
    (path === "/" && item.href === "/overview") ||
    path === item.href ||
    (item.href !== "/" && path.startsWith(item.href + "/"));
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      className={`nav-item ${active ? "active" : ""}`}
      aria-current={active ? "page" : undefined}
    >
      <item.icon size={17} aria-hidden="true" />
      {item.label}
    </Link>
  );
}
export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const publicPage = path === "/login" || path.startsWith("/invite/");
  const [mobile, setMobile] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () =>
      api<{
        actor: string;
        email: string;
        role: string;
        environment: string;
        auth_mode: string;
      }>("/session"),
    staleTime: 30000,
    enabled: !publicPage,
  });
  const incidents = useQuery({
    queryKey: ["alert-summary"],
    queryFn: () => api<AlertSummary>("/alerts/summary"),
    enabled: !publicPage && session.isSuccess,
    refetchInterval: 30000,
  });
  const openCount = incidents.data?.active_count;
  useEffect(() => {
    const themeFrame = window.requestAnimationFrame(() =>
      setTheme(
        document.documentElement.dataset.theme === "dark" ? "dark" : "light",
      ),
    );
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobile(false);
    };
    window.addEventListener("keydown", handle);
    return () => {
      window.cancelAnimationFrame(themeFrame);
      window.removeEventListener("keydown", handle);
    };
  }, []);
  useEffect(() => {
    if (
      !publicPage &&
      session.error instanceof ApiError &&
      session.error.code === "UNAUTHENTICATED"
    ) {
      router.replace("/login");
    }
  }, [publicPage, router, session.error]);
  const toggleTheme = () => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("cluecdc-theme", nextTheme);
    setTheme(nextTheme);
  };
  const group = groups.find((group) =>
    group.items.some(
      (item) => path === item.href || path.startsWith(item.href + "/"),
    ),
  );
  const current = group?.items.find(
    (item) => path === item.href || path.startsWith(item.href + "/"),
  );
  const close = () => setMobile(false);
  if (publicPage) return <>{children}</>;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to workspace
      </a>
      {mobile && (
        <button
          className="nav-backdrop"
          aria-label="Close navigation"
          onClick={close}
        />
      )}
      <aside className={`sidebar ${mobile ? "mobile-open" : ""}`}>
        <Link href="/" className="brand" onClick={close}>
          <span className="brand-mark">
            <GitBranch size={27} />
          </span>
          <div>
            <strong>
              Clue<span>CDC</span>
            </strong>
          </div>
        </Link>
        <button
          className="mobile-close"
          onClick={close}
          aria-label="Close sidebar"
        >
          <X size={18} />
        </button>
        <nav aria-label="Main navigation">
          <div className="signal-navigation">
            {groups.map((group) => (
              <section
                className="sidebar-group"
                key={group.id}
                aria-labelledby={`nav-${group.id}`}
              >
                <h2 id={`nav-${group.id}`}>{group.label}</h2>
                {group.items
                  .filter(
                    (item) => !item.adminOnly || session.data?.role === "Admin",
                  )
                  .map((item) => (
                    <SidebarItem
                      key={item.href}
                      item={item}
                      path={path}
                      onNavigate={close}
                    />
                  ))}
              </section>
            ))}
          </div>
        </nav>
        <div className="sidebar-bottom">
          <Link href="/settings" className="user">
            <span className="avatar">
              {session.data?.actor.slice(0, 2).toUpperCase() || "?"}
            </span>
            <div>
              <strong>
                {session.data?.actor || "Authentication required"}
              </strong>
              <small>{session.data?.role || "Open Settings to sign in"}</small>
            </div>
            <Settings size={15} />
          </Link>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <button
            className="mobile-menu icon-button"
            onClick={() => setMobile(true)}
            aria-label="Open navigation"
            aria-expanded={mobile}
          >
            <Menu size={20} />
          </button>
          <div className="breadcrumb">
            <span>ClueCDC</span>
            <ChevronRight size={13} />
            <strong>
              {current?.label || (path === "/" ? "Overview" : "Settings")}
            </strong>
          </div>
          <div className="environment-indicator">
            <span
              className={`connection-dot ${session.isError ? "offline" : session.isPending ? "pending" : ""}`}
              title={
                session.isError
                  ? "API unavailable"
                  : session.isPending
                    ? "Checking API"
                    : "API connected"
              }
            />
            <span>
              <small>Environment</small>
              <strong>
                {session.data?.environment ||
                  (session.isError ? "Offline" : "Connecting")}
              </strong>
            </span>
          </div>
          <div className="alert-indicator">
            <button
              className="icon-button notifications"
              aria-label={`Recent alerts${openCount === undefined ? "" : `: ${openCount} active`}`}
              aria-expanded={alertsOpen}
              onClick={() => setAlertsOpen((value) => !value)}
              title="Recent alerts"
            >
              <Bell size={17} />
              {!!openCount && (
                <span className="notification-count">
                  {openCount > 99 ? "99+" : openCount}
                </span>
              )}
            </button>
            {alertsOpen && (
              <div className="recent-alerts-popover">
                <div className="recent-alerts-heading">
                  <strong>Recent alerts</strong>
                  <small>{openCount || 0} active</small>
                </div>
                {incidents.data?.recent.length ? (
                  incidents.data.recent.map((alert) => (
                    <Link
                      href={`/alerts/${alert.id}`}
                      key={alert.id}
                      onClick={() => setAlertsOpen(false)}
                    >
                      <span
                        className={`alert-severity-dot ${alert.severity}`}
                      />
                      <span>
                        <strong>
                          {alert.pipeline_name ||
                            alert.source_name ||
                            alert.title}
                        </strong>
                        <small>
                          {alert.title} · {relativeTime(alert.last_seen_at)}
                        </small>
                      </span>
                    </Link>
                  ))
                ) : (
                  <p>Everything is running normally.</p>
                )}
                <Link
                  href="/alerts"
                  className="view-all-alerts"
                  onClick={() => setAlertsOpen(false)}
                >
                  View all alerts
                </Link>
              </div>
            )}
          </div>
          <button
            type="button"
            className="icon-button theme-toggle"
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            title={`${theme === "dark" ? "Light" : "Dark"} theme`}
            onClick={toggleTheme}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <div className="user-menu">
            <button
              type="button"
              className="avatar topbar-avatar"
              aria-label="Current user menu"
              aria-expanded={userOpen}
              onClick={() => setUserOpen((value) => !value)}
            >
              {session.data?.actor.slice(0, 2).toUpperCase() || "?"}
            </button>
            {userOpen && (
              <div className="user-menu-popover">
                <strong>{session.data?.email || session.data?.actor}</strong>
                <small>Role: {session.data?.role}</small>
                <button
                  type="button"
                  onClick={async () => {
                    await api("/auth/logout", { method: "POST" });
                    setUserOpen(false);
                    router.replace("/login");
                    router.refresh();
                  }}
                >
                  <LogOut size={15} /> Sign out
                </button>
              </div>
            )}
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
