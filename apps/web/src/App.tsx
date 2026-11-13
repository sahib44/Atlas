import { createContext, useContext, useState, useEffect, useRef, lazy, Suspense } from "react";
import {
  Routes,
  Route,
  NavLink,
  Navigate,
  useNavigate,
  useLocation,
  Link,
} from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  UserRoundPlus,
  Upload,
  Copy,
  Send,
  Inbox,
  LifeBuoy,
  History,
  LogOut,
  ShieldCheck,
  Menu,
  Moon,
  Sun,
  FolderKanban,
} from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useData, useAction, User, Page, can, label } from "./api";
import { Field, ErrorBox, Loading, HistoryBack, Modal } from "./ui";
import { Dashboard, AuditPage } from "./Overview";
import { JudgeConversation, Notifications } from "./Inbox";
import { Profile } from "./Profile";
import brandMark from "./mist-deck-mark.svg?no-inline";
import "./judge-workspace.css";
import { Imports, ImportWizard, Duplicates, DuplicateDetail } from "./Imports";
import { Campaigns, CampaignNew, CampaignDetail, Outbox } from "./Campaigns";
import { Tickets, TicketNew } from "./Tickets";
import { Competitions, CompetitionCategory, PilotDetail, DeletedCompetitions } from "./Pilots";
const JudgeDirectory = lazy(() => import("./JudgeDirectory").then(module => ({ default: module.JudgeDirectory })));
const JudgeRecruitment = lazy(() => import("./JudgeRecruitment").then(module => ({ default: module.JudgeRecruitment })));

const Auth = createContext<User | undefined>(undefined);
export const useUser = () => useContext(Auth);
const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});
type Theme = "light" | "dark";
function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  return (
    <button
      type="button"
      className="theme-toggle"
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      aria-pressed={theme === "dark"}
      title={theme === "dark" ? "Light mode" : "Dark mode"}
      onClick={onToggle}
    >
      {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
function Login({ theme, onToggleTheme }: { theme: Theme; onToggleTheme: () => void }) {
  const action = useAction();
  const navigate = useNavigate();
  const form = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });
  return (
    <div className="login-page">
      <div className="login-theme-toggle">
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>
      <main className="login-card">
        <div className="login-identity">
          <img className="brand-symbol" src={brandMark} alt="" width={56} height={56} />
          <h1>MIST Deck</h1>
        </div>
        <h2>Sign in</h2>
        <form
          onSubmit={form.handleSubmit(async (values) => {
            try {
              await action.mutateAsync({ path: "/auth/login", body: values });
              navigate("/dashboard");
            } catch {}
          })}
        >
          <Field label="Email">
            <input
              type="email"
              autoComplete="username"
              {...form.register("email")}
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              autoComplete="current-password"
              {...form.register("password")}
            />
          </Field>
          <ErrorBox
            error={
              Object.values(form.formState.errors)[0]?.message || action.error
            }
          />
          <button className="primary" disabled={action.isPending}>
            Sign in
          </button>
        </form>
      </main>
    </div>
  );
}
const navGroups = [
  {
    label: "Workspace",
    items: [
      ["/dashboard", "Overview", LayoutDashboard, ""],
      ["/competitions", "Competitions", FolderKanban, "judges"],
      ["/recruitment", "Judge Recruitment", UserRoundPlus, "judges"],
      ["/judges", "Judge Directory", Users, "judges"],
      ["/campaigns", "Campaigns", Send, "campaigns"],
      ["/tickets", "Tickets", LifeBuoy, "tickets"],
    ],
  },
  {
    label: "Operations",
    items: [
      ["/imports", "Imports", Upload, "imports"],
      ["/duplicates", "Duplicate review", Copy, "imports"],
      ["/outbox", "Sandbox outbox", Inbox, "outbox"],
      ["/audit", "Audit trail", History, "audit"],
    ],
  },
] as const;

function WorkspaceNavigation({ user, onNavigate }: { user: User; onNavigate?: () => void }) {
  return <div className="sidebar-navigation">
    {navGroups.map(group => {
      const items = group.items.filter(([path, , , permission]) =>
        !permission || can(user, permission) || (path === "/judges" && can(user, "imports")),
      );
      if (!items.length) return null;
      return <nav className="nav-group" aria-label={group.label} key={group.label}>
        {group.label !== "Workspace" && <div className="nav-label">{group.label}</div>}
        {items.map(([path, name, Icon]) => (
          <NavLink onClick={onNavigate} key={path} to={path} title={name} aria-label={name}>
            <Icon size={18} aria-hidden="true" />
            <span className="nav-text">{name}</span>
          </NavLink>
        ))}
      </nav>;
    })}
  </div>;
}

function WorkspaceAccount({ user, onSignOut, onOpenProfile }: { user: User; onSignOut: () => void; onOpenProfile?: () => void }) {
  const { pathname } = useLocation();
  const initials = user.full_name.trim().split(/\s+/).slice(0, 2).map(name => name[0]).join("");
  return <div className="workspace-account">
    <Link className="workspace-profile" to="/profile" state={pathname === "/profile" ? undefined : { fromWorkspace: true }}
      onClick={event => {
        if (pathname === "/profile") event.preventDefault();
        onOpenProfile?.();
      }}
      aria-label="Open profile" aria-current={pathname === "/profile" ? "page" : undefined} title="Open profile">
      <span className="avatar" aria-hidden="true">{initials}</span>
      <div className="workspace-copy"><strong>{user.full_name}</strong><small>{label(user.role)}</small></div>
    </Link>
    <button type="button" className="account-sign-out" aria-label="Sign out" title="Sign out" onClick={onSignOut}>
      <LogOut size={17} aria-hidden="true" />
    </button>
  </div>;
}
function Guard({
  permission,
  children,
}: {
  permission: string;
  children: React.ReactNode;
}) {
  const user = useUser();
  return can(user, permission) ? (
    children
  ) : (
    <div className="empty">
      <h1>Access restricted</h1>
      <p>Your role does not have permission to open this module.</p>
      <Link to="/dashboard">Return to overview</Link>
    </div>
  );
}
function BackgroundNotice() {
  const user = useUser();
  const messages = useData<Page>("/outbox?page_size=100", can(user, "outbox"));
  const previous = useRef<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!messages.data) return;
    const changed = messages.data.items.filter(
      (m) =>
        ["queued", "sending"].includes(previous.current[m.id]) &&
        ["sent", "failed"].includes(m.status),
    );
    if (changed.length)
      setNotice(
        `${changed.length} sandbox message${changed.length === 1 ? "" : "s"} finished processing. Check the outbox for delivery results.`,
      );
    previous.current = Object.fromEntries(
      messages.data.items.map((m) => [m.id, m.status]),
    );
  }, [messages.data]);
  return notice ? (
    <div className="toast" role="status">
      <span>{notice}</span>
      <button aria-label="Dismiss notification" onClick={() => setNotice("")}>
        ×
      </button>
    </div>
  ) : null;
}
export default function App() {
  const auth = useData<User>("/auth/me");
  const action = useAction();
  const [menu, setMenu] = useState(false);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const [collapsed, setCollapsed] = useState(
    () => window.localStorage.getItem("mist-sidebar-collapsed") === "true",
  );
  const { pathname } = useLocation();
  const judgeWorkspace = pathname === "/recruitment" || pathname === "/judges";
  const ticketWorkspace = /^\/tickets(?:\/(?!new$)[^/]+)?$/.test(pathname);
  const [theme, setTheme] = useState<Theme>(() =>
    window.localStorage.getItem("mist-theme") === "dark" ? "dark" : "light",
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("mist-theme", theme);
  }, [theme]);
  useEffect(() => {
    window.localStorage.setItem("mist-sidebar-collapsed", String(collapsed));
  }, [collapsed]);
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 801px)");
    const closeOnDesktop = () => { if (desktop.matches) setMenu(false); };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);
  const toggleTheme = () => setTheme((current) => current === "dark" ? "light" : "dark");
  if (auth.isPending) return <Loading />;
  if (!auth.data || auth.error)
    return (
      <Routes>
        <Route path="/login" element={<Login theme={theme} onToggleTheme={toggleTheme} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  const user = auth.data;
  const signOut = () => action.mutate({ path: "/auth/logout" });
  const closeNavigation = () => {
    setMenu(false);
    requestAnimationFrame(() => menuTrigger.current?.focus());
  };
  return (
    <Auth.Provider value={user}>
      <BackgroundNotice />
      <div className={collapsed ? "app sidebar-collapsed" : "app"}>
        <aside className={collapsed ? "sidebar collapsed" : "sidebar"}>
          <div className="sidebar-header">
            <button
              type="button"
              className="brand"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              onClick={() => setCollapsed((value) => !value)}
            >
              <img className="brand-symbol" src={brandMark} alt="" width={42} height={42} />
              <span className="brand-copy">MIST Deck</span>
            </button>
          </div>
          <WorkspaceNavigation user={user} />
          <WorkspaceAccount user={user} onSignOut={signOut} />
        </aside>
        {menu && <Modal
          title={<span className="drawer-brand"><img className="brand-symbol" src={brandMark} alt="" width={32} height={32} /> MIST Deck</span>}
          closeLabel="Close navigation"
          className="navigation-drawer"
          onClose={closeNavigation}
        >
          <WorkspaceNavigation user={user} onNavigate={() => setMenu(false)} />
          <WorkspaceAccount user={user} onSignOut={signOut} onOpenProfile={() => setMenu(false)} />
        </Modal>}
        <div className={`main-shell${judgeWorkspace ? " judge-app" : ""}${ticketWorkspace ? " tickets-app" : ""}`}>
          <header className="topbar">
            <button
              ref={menuTrigger}
              className="menu-button"
              aria-label="Toggle navigation"
              aria-haspopup="dialog"
              aria-expanded={menu}
              onClick={() => setMenu(!menu)}
            >
              <Menu size={20} />
            </button>
            {pathname !== "/profile" && <HistoryBack />}
            <div className="topbar-tools">
              <div className="sandbox" title="Sandbox email mode"><ShieldCheck size={15} /> <span>SANDBOX EMAIL MODE</span></div>
              <Notifications />
              <ThemeToggle theme={theme} onToggle={toggleTheme} />
            </div>
          </header>
          <main className="content">
            <Routes>
              <Route
                path="/login"
                element={<Navigate to="/dashboard" replace />}
              />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/profile" element={<Profile user={user} />} />
              <Route path="/judges" element={<Suspense fallback={<Loading />}><JudgeDirectory /></Suspense>} />
              <Route path="/recruitment" element={<Guard permission="judges"><Suspense fallback={<Loading />}><JudgeRecruitment /></Suspense></Guard>} />
              <Route path="/contacts" element={<Navigate to="/judges" replace />} />
              <Route path="/judges/:id" element={<Guard permission="campaigns"><JudgeConversation /></Guard>} />
              <Route
                path="/imports"
                element={
                  <Guard permission="imports">
                    <Imports />
                  </Guard>
                }
              />
              <Route
                path="/imports/new"
                element={
                  <Guard permission="imports">
                    <ImportWizard />
                  </Guard>
                }
              />
              <Route
                path="/imports/:id"
                element={
                  <Guard permission="imports">
                    <ImportWizard />
                  </Guard>
                }
              />
              <Route
                path="/duplicates"
                element={
                  <Guard permission="imports">
                    <Duplicates />
                  </Guard>
                }
              />
              <Route
                path="/duplicates/:id"
                element={
                  <Guard permission="imports">
                    <DuplicateDetail />
                  </Guard>
                }
              />
              <Route
                path="/competitions"
                element={<Guard permission="judges"><Competitions /></Guard>}
              />
              <Route
                path="/competitions/:category"
                element={<Guard permission="judges"><CompetitionCategory /></Guard>}
              />
              <Route
                path="/competitions/:category/:id"
                element={<Guard permission="judges"><PilotDetail /></Guard>}
              />
              <Route path="/pilots" element={<Navigate to="/competitions" replace />} />
              <Route path="/pilots/:id" element={<Guard permission="judges"><PilotDetail /></Guard>} />
              <Route path="/campaigns/conversations/judges/:id" element={<Guard permission="campaigns"><JudgeConversation /></Guard>} />
              <Route
                path="/campaigns"
                element={
                  <Guard permission="campaigns">
                    <Campaigns />
                  </Guard>
                }
              />
              <Route
                path="/campaigns/new"
                element={
                  <Guard permission="campaigns">
                    <CampaignNew />
                  </Guard>
                }
              />
              <Route
                path="/campaigns/:id"
                element={
                  <Guard permission="campaigns">
                    <CampaignDetail />
                  </Guard>
                }
              />
              <Route
                path="/outbox"
                element={
                  <Guard permission="outbox">
                    <Outbox />
                  </Guard>
                }
              />
              <Route
                path="/tickets"
                element={
                  <Guard permission="tickets">
                    <Tickets />
                  </Guard>
                }
              />
              <Route
                path="/tickets/new"
                element={
                  <Guard permission="tickets">
                    <TicketNew />
                  </Guard>
                }
              />
              <Route
                path="/tickets/:id"
                element={
                  <Guard permission="tickets">
                    <Tickets />
                  </Guard>
                }
              />
              <Route
                path="/audit"
                element={
                  <Guard permission="audit">
                    <AuditPage />
                  </Guard>
                }
              />
              <Route
                path="/competitions/deletions"
                element={<Guard permission="judges"><DeletedCompetitions /></Guard>}
              />
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route
                path="*"
                element={
                  <div className="empty">
                    <h1>Page not found</h1>
                    <Link to="/dashboard">Return to overview</Link>
                  </div>
                }
              />
            </Routes>
          </main>
          <footer className="footer">
            MIST Deck <span>Local prototype · Sandbox email</span>
          </footer>
        </div>
      </div>
    </Auth.Provider>
  );
}
