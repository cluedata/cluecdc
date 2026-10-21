"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bell,
  Box,
  ChevronDown,
  ChevronRight,
  Database,
  FileClock,
  GitBranch,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Radio,
  Settings,
  Sun,
  Users,
  Workflow,
  Send,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { AlertSummary } from "@cluecdc/contracts";
import { api, ApiError, relativeTime } from "@/lib/api";
import { hasPermission, requiredPermissionForPath } from "@/lib/auth";
import { BrandMark } from "./brand-mark";
type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  permission: string;
};
const overview: NavItem = {
  label: "Overview",
  href: "/overview",
  icon: LayoutDashboard,
  permission: "overview.read",
};
const groups: { id: string; label: string; items: NavItem[] }[] = [
  {
    id: "workspace",
    label: "Workspace",
    items: [
      {
        label: "Pipelines",
        href: "/pipelines",
        icon: GitBranch,
        permission: "pipelines.read",
      },
      {
        label: "Deliveries",
        href: "/deliveries",
        icon: Send,
        permission: "deliveries.read",
      },
    ],
  },
  {
    id: "connections",
    label: "Connections",
    items: [
      {
        label: "Sources",
        href: "/sources",
        icon: Database,
        permission: "sources.read",
      },
      {
        label: "Destinations",
        href: "/destinations",
        icon: Workflow,
        permission: "destinations.read",
      },
      {
        label: "Kafka Clusters",
        href: "/kafka/clusters",
        icon: Radio,
        permission: "*",
      },
      {
        label: "Connect Clusters",
        href: "/connect/clusters",
        icon: Box,
        permission: "*",
      },
    ],
  },
  {
    id: "operations",
    label: "Operations",
    items: [
      {
        label: "Monitoring",
        href: "/monitoring",
        icon: Activity,
        permission: "*",
      },
      { label: "Alerts", href: "/alerts", icon: Bell, permission: "*" },
      {
        label: "Audit",
        href: "/operations/audit",
        icon: FileClock,
        permission: "*",
      },
    ],
  },
  {
    id: "system",
    label: "System",
    items: [
      { label: "User", href: "/settings/users", icon: Users, permission: "*" },
      { label: "Settings", href: "/settings", icon: Settings, permission: "*" },
    ],
  },
];
function SidebarItem({
  item,
  active,
  onNavigate,
  collapsed,
}: {
  item: NavItem;
  active: boolean;
  onNavigate: () => void;
  collapsed: boolean;
}) {
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      className={`nav-item ${active ? "active" : ""}`}
      aria-current={active ? "page" : undefined}
      title={collapsed ? item.label : undefined}
    >
      <item.icon size={17} aria-hidden="true" />
      <span className="nav-label">{item.label}</span>
    </Link>
  );
}
export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const publicPage = path === "/login" || path.startsWith("/invite/");
  const [mobile, setMobile] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [closedGroups, setClosedGroups] = useState<Record<string, boolean>>({});
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
        permissions: string[];
      }>("/session"),
    staleTime: 30000,
    enabled: !publicPage,
  });
  const incidents = useQuery({
    queryKey: ["alert-summary"],
    queryFn: () => api<AlertSummary>("/alerts/summary"),
    enabled: !publicPage && session.data?.role === "Admin",
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
    const sidebarFrame = window.requestAnimationFrame(() => {
      if (window.localStorage.getItem("cluecdc-sidebar") === "collapsed") {
        setSidebarCollapsed(true);
      }
    });
    return () => window.cancelAnimationFrame(sidebarFrame);
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
  const requiredPermission = requiredPermissionForPath(path);
  const authorized =
    requiredPermission === null ||
    hasPermission(session.data?.permissions, requiredPermission);
  useEffect(() => {
    if (!publicPage && session.isSuccess && !authorized) {
      router.replace("/overview");
    }
  }, [authorized, publicPage, router, session.isSuccess]);
  const toggleTheme = () => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("cluecdc-theme", nextTheme);
    setTheme(nextTheme);
  };
  const current = [overview, ...groups.flatMap((group) => group.items)]
    .filter((item) => {
      const currentPath = path === "/" ? "/overview" : path;
      return (
        currentPath === item.href || currentPath.startsWith(item.href + "/")
      );
    })
    .sort((a, b) => b.href.length - a.href.length)[0];
  const visibleGroups = groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) =>
        hasPermission(session.data?.permissions, item.permission),
      ),
    }))
    .filter((group) => group.items.length);
  const close = () => setMobile(false);
  const toggleSidebar = () => {
    const next = !sidebarCollapsed;
    setSidebarCollapsed(next);
    window.localStorage.setItem(
      "cluecdc-sidebar",
      next ? "collapsed" : "expanded",
    );
  };
  if (publicPage) return <>{children}</>;
  if (session.isSuccess && !authorized) return null;
  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
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
      <aside
        className={`sidebar ${mobile ? "mobile-open" : ""}`}
        aria-label="Application sidebar"
      >
        <Link href="/" className="brand" onClick={close}>
          <span className="brand-mark">
            <BrandMark priority />
          </span>
          <div className="brand-product">
            <strong>
              Clue<span>CDC</span>
            </strong>
            <small>Data movement</small>
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
            {hasPermission(session.data?.permissions, overview.permission) && (
              <SidebarItem
                item={overview}
                active={current?.href === overview.href}
                onNavigate={close}
                collapsed={sidebarCollapsed}
              />
            )}
            {visibleGroups.map((group) => {
              const open =
                !closedGroups[group.id] || (sidebarCollapsed && !mobile);
              return (
                <section
                  className="sidebar-group"
                  key={group.id}
                  aria-labelledby={`nav-${group.id}`}
                >
                  <button
                    type="button"
                    className="sidebar-group-toggle"
                    aria-expanded={open}
                    aria-controls={`nav-${group.id}-items`}
                    onClick={() =>
                      setClosedGroups((value) => ({
                        ...value,
                        [group.id]: !value[group.id],
                      }))
                    }
                    title={sidebarCollapsed ? group.label : undefined}
                  >
                    <span id={`nav-${group.id}`}>{group.label}</span>
                    <ChevronDown size={14} aria-hidden="true" />
                  </button>
                  <div id={`nav-${group.id}-items`} hidden={!open}>
                    {group.items.map((item) => (
                      <SidebarItem
                        key={item.href}
                        item={item}
                        active={current?.href === item.href}
                        onNavigate={close}
                        collapsed={sidebarCollapsed}
                      />
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        </nav>
        <div className="sidebar-bottom">
          <button
            type="button"
            className="sidebar-collapse"
            onClick={toggleSidebar}
            aria-label={
              sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"
            }
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? (
              <PanelLeftOpen size={16} />
            ) : (
              <PanelLeftClose size={16} />
            )}
            <span>Collapse sidebar</span>
          </button>
          {session.data?.role === "Admin" ? (
            <Link href="/settings" className="user">
              <span className="avatar">
                {session.data?.actor.slice(0, 2).toUpperCase() || "?"}
              </span>
              <div>
                <strong>
                  {session.data?.actor || "Authentication required"}
                </strong>
                <small>
                  {session.data?.role || "Open Settings to sign in"}
                </small>
              </div>
              <Settings size={15} />
            </Link>
          ) : (
            <div className="user">
              <span className="avatar">
                {session.data?.actor.slice(0, 2).toUpperCase() || "?"}
              </span>
              <div>
                <strong>
                  {session.data?.actor || "Authentication required"}
                </strong>
                <small>
                  {session.data?.role || "Open Settings to sign in"}
                </small>
              </div>
            </div>
          )}
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
          {session.data?.role === "Admin" && (
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
          )}
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
