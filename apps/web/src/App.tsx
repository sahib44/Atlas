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
  Mail,
  Inbox,
  LifeBuoy,
  History,
  LogOut,
  ShieldCheck,
  Menu,
  Moon,
  Sun,
  FolderKanban,
  Eye,
  EyeOff,
  LoaderCircle,
} from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { SessionScope, useSession, useData, useAction, User, Page, can, label } from "./api";
import { useQueryClient } from "@tanstack/react-query";
import { ErrorBox, Loading, HistoryBack, Modal } from "./ui";
import { Dashboard, AuditPage } from "./Overview";
import { JudgeConversation, Notifications } from "./Inbox";
import { Profile } from "./Profile";
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
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});
const appearanceOptions = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "purple-dark", label: "Purple Dark", icon: Moon },
] as const;
type Theme = (typeof appearanceOptions)[number]["value"];
type AppearanceProps = { theme: Theme; onChangeTheme: (theme: Theme) => void };

function loadTheme(): Theme {
  const savedAppearance = window.localStorage.getItem("mist-appearance");
  const savedOption = appearanceOptions.find(option => option.value === savedAppearance);
  if (savedOption) return savedOption.value;
  return window.localStorage.getItem("mist-theme") === "dark" ? "purple-dark" : "light";
}

function AppearanceControl({ theme, onChangeTheme }: AppearanceProps) {
  const [open, setOpen] = useState(false);
  const control = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const currentLabel = appearanceOptions.find(option => option.value === theme)?.label;
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !control.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);
  return (
    <div
      ref={control}
      className="appearance-control"
      onBlur={event => {
        if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={event => {
        if (event.key !== "Escape" || !open) return;
        event.stopPropagation();
        setOpen(false);
        trigger.current?.focus();
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="theme-toggle"
        aria-label="Appearance"
        aria-expanded={open}
        title={`Appearance: ${currentLabel}`}
        onClick={() => setOpen(value => !value)}
      >
        {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
      </button>
      {open && (
        <div className="appearance-options" role="group" aria-label="Theme">
          {appearanceOptions.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              aria-pressed={theme === value}
              onClick={() => {
                onChangeTheme(value);
                setOpen(false);
                trigger.current?.focus();
              }}
            >
              <Icon size={16} aria-hidden="true" /> {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
function AtlasBrand({ theme, variant, alt = "" }: { theme: Theme; variant: "stacked-compact" | "compact" | "horizontal-compact"; alt?: string }) {
  const mode = theme === "light" ? "light" : "dark";
  const dimensions = { "stacked-compact": [284, 146], compact: [132, 78], "horizontal-compact": [284, 46] };
  const [width, height] = dimensions[variant];
  return <img className={`brand-symbol atlas-${variant}`} src={`/brand/atlas-${variant}-${mode}.svg`} alt={alt} width={width} height={height} />;
}

function ConstellationBackdrop() {
  return (
    <svg className="constellation-backdrop" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
      <path d="M-50 344 122 403 249 268 346 411 254 554 104 510-50 632M122 403 104 510 160 672 254 554 314 704 450 758M346 411 426 226 560 159M249 268 426 226M254 554 450 758M314 704 160 672
        M1490 291 1322 331 1194 242 1060 369 982 206M1490 565 1354 470 1322 331 1060 369 1164 521 1354 470 1290 650 1164 521 1015 682 923 759M1194 242 1164 521M1290 650 1434 723M1015 682 1290 650" />
      <circle cx="122" cy="403" r="3" /><circle cx="249" cy="268" r="4" /><circle cx="346" cy="411" r="2" />
      <circle cx="104" cy="510" r="2.5" /><circle cx="254" cy="554" r="3.5" /><circle cx="160" cy="672" r="2" />
      <circle cx="314" cy="704" r="2.5" /><circle cx="450" cy="758" r="3" /><circle cx="426" cy="226" r="3" /><circle cx="560" cy="159" r="2" />
      <circle cx="982" cy="206" r="2.5" /><circle cx="1194" cy="242" r="3.5" /><circle cx="1322" cy="331" r="3" />
      <circle cx="1060" cy="369" r="2" /><circle cx="1164" cy="521" r="3.5" /><circle cx="1354" cy="470" r="2" />
      <circle cx="1015" cy="682" r="2.5" /><circle cx="1290" cy="650" r="3" /><circle cx="923" cy="759" r="2" />
    </svg>
  );
}

function AccountFrame({ theme, onChangeTheme, title, description, children }: AppearanceProps & {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="login-page constellation-surface">
      <ConstellationBackdrop />
      <div className="login-theme-toggle">
        <AppearanceControl theme={theme} onChangeTheme={onChangeTheme} />
      </div>
      <main className="login-card">
        <h1 className="login-identity" aria-label="MIST Atlas"><AtlasBrand theme={theme} variant="stacked-compact" /></h1>
        <h2>{title}</h2>
        <p className="login-subtitle">{description}</p>
        {children}
      </main>
    </div>
  );
}

function Login({ theme, onChangeTheme }: AppearanceProps) {
  const action = useAction();
  const navigate = useNavigate();
  const [passwordVisible, setPasswordVisible] = useState(false);
  const form = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });
  const { email: emailError, password: passwordError } = form.formState.errors;
  return (
    <AccountFrame theme={theme} onChangeTheme={onChangeTheme} title="Sign in" description="Your MIST NJ workspace.">
        <form
          noValidate
          aria-busy={action.isPending}
          onSubmit={form.handleSubmit(async (values) => {
            try {
              await action.mutateAsync({ path: "/auth/login", body: values });
              navigate("/dashboard");
            } catch {}
          })}
        >
          <div className="field">
            <label htmlFor="login-email">Email</label>
            <input
              id="login-email"
              type="email"
              autoComplete="username"
              required
              aria-invalid={!!emailError}
              aria-describedby={emailError ? "login-email-error" : undefined}
              {...form.register("email")}
            />
            {emailError && <span id="login-email-error" className="login-field-error" role="alert">{emailError.message}</span>}
          </div>
          <div className="field">
            <label htmlFor="login-password">Password</label>
            <div className="login-password">
              <input
                id="login-password"
                type={passwordVisible ? "text" : "password"}
                autoComplete="current-password"
                required
                aria-invalid={!!passwordError}
                aria-describedby={passwordError ? "login-password-error" : undefined}
                {...form.register("password")}
              />
              <button
                type="button"
                className="login-password-toggle"
                aria-controls="login-password"
                aria-label={passwordVisible ? "Hide password" : "Show password"}
                onClick={() => setPasswordVisible(visible => !visible)}
              >
                {passwordVisible ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
              </button>
            </div>
            {passwordError && <span id="login-password-error" className="login-field-error" role="alert">{passwordError.message}</span>}
          </div>
          <ErrorBox error={!emailError && !passwordError ? action.error : null} />
          <button type="submit" className="primary login-submit" disabled={action.isPending}>
            {action.isPending && <LoaderCircle size={17} aria-hidden="true" />}
            {action.isPending ? "Signing in…" : "Sign in"}
          </button>
        </form>
    </AccountFrame>
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
      ["/campaigns", "Email Outreach", Mail, "campaigns"],
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
  const auth = useSession();
  const cache = useQueryClient();
  const user = auth.data;
  const scope = user ? JSON.stringify([user.id, user.role, [...user.permissions].sort()]) : "";
  const previousScope = useRef("");
  useEffect(() => {
    const previous = previousScope.current;
    if (previous && previous !== scope) {
      cache.removeQueries({ predicate: query => query.queryKey[1] === previous });
    }
    previousScope.current = scope;
  }, [cache, scope]);
  const action = useAction();
  const [menu, setMenu] = useState(false);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const [collapsed, setCollapsed] = useState(
    () => window.localStorage.getItem("mist-sidebar-collapsed") === "true",
  );
  const { pathname } = useLocation();
  const judgeWorkspace = pathname === "/recruitment" || pathname === "/judges";
  const ticketWorkspace = /^\/tickets(?:\/(?!new$)[^/]+)?$/.test(pathname);
  const [theme, setTheme] = useState(loadTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("mist-appearance", theme);
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
  if (auth.isPending) return <Loading />;
  if (!user)
    return (
      <Routes>
        <Route path="/login" element={<Login theme={theme} onChangeTheme={setTheme} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  const signOut = () => action.mutate({ path: "/auth/logout" });
  const closeNavigation = () => {
    setMenu(false);
    requestAnimationFrame(() => menuTrigger.current?.focus());
  };
  return (
    <SessionScope.Provider key={scope} value={scope}>
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
              <AtlasBrand theme={theme} variant={collapsed ? "compact" : "horizontal-compact"} />
            </button>
          </div>
          <WorkspaceNavigation user={user} />
          <WorkspaceAccount user={user} onSignOut={signOut} />
        </aside>
        {menu && <Modal
          title={<span className="drawer-brand"><AtlasBrand theme={theme} variant="horizontal-compact" alt="MIST Atlas" /></span>}
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
              <AppearanceControl theme={theme} onChangeTheme={setTheme} />
            </div>
          </header>
          <main className="content constellation-surface">
            <ConstellationBackdrop />
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
            MIST Atlas <span>Local prototype · Sandbox email</span>
          </footer>
        </div>
      </div>
    </Auth.Provider>
    </SessionScope.Provider>
  );
}
