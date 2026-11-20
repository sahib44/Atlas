import { useEffect, useRef, useState, type FormEvent } from "react";
import { History, MailPlus, UserRound } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, date, label, useAction, useData, type Department } from "./api";
import { Empty, ErrorBox, Field, Heading, Loading, Modal } from "./ui";
import "./accounts.css";

type Position = Department & {
  state: "unassigned" | "invited" | "active" | "deactivated" | "revoked";
  email: string | null;
  assignment_id: string | null;
  invitation_status: "none" | "pending" | "accepted" | "expired" | "revoked";
  delivery_status: string | null;
  expires_at: string | null;
};
type AccountHistory = {
  id: string;
  summary: string;
  created_at: string;
  email: string | null;
};

const positionLabels: Record<Position["state"], string> = {
  unassigned: "Unassigned",
  invited: "Awaiting activation",
  active: "Active",
  deactivated: "Deactivated",
  revoked: "Revoked",
};
const available = (position: Position) => ["unassigned", "revoked"].includes(position.state);
const mistEmail = /^[^@\s]+@getmistified\.com$/i;

const operations = {
  invite: { title: "Invite to", submit: "Send invitation", notice: "Invitation queued." },
  resend: {
    title: "Resend invitation for", submit: "Resend invitation", notice: "Replacement invitation queued.",
    detail: "The previous activation link will expire immediately.",
  },
  "revoke-invitation": {
    title: "Revoke invitation for", submit: "Revoke invitation", notice: "Invitation revoked.",
    detail: "The activation link will stop working and the position will be available for a new invitation.",
    destructive: true,
  },
  deactivate: {
    title: "Deactivate account for", submit: "Deactivate account", notice: "Account deactivated.",
    detail: "This ends all sessions for this account and reserves the position for the same person.",
    destructive: true,
  },
  reactivate: {
    title: "Reactivate account for", submit: "Reactivate account", notice: "Account reactivated.",
    detail: "This person will be able to sign in again with their existing account.",
  },
  "revoke-account": {
    title: "Revoke account for", submit: "Revoke account", notice: "Account revoked.",
    detail: "This ends all sessions and frees the position for a replacement. The assignment history is kept.",
    destructive: true,
  },
  reassign: {
    title: "Reassign", submit: "Reassign account", notice: "Department assignment updated.",
    detail: "This ends the current assignment and makes the selected department their primary responsibility.",
  },
} satisfies Record<string, { title: string; submit: string; notice: string; detail?: string; destructive?: boolean }>;
type Operation = keyof typeof operations;

function PositionHistory({ position, onClose }: { position: Position; onClose: () => void }) {
  const history = useData<{ items: AccountHistory[] }>(`/accounts/positions/${encodeURIComponent(position.slug)}/history`);
  return <Modal title={`${position.name} history`} onClose={onClose} className="account-dialog">
    <ErrorBox error={history.error} />
    {history.isPending && <Loading />}
    {history.data && (history.data.items.length ? (
      <ul className="account-history">
        {history.data.items.map(event => <li key={event.id}>
          <div><strong>{event.summary}</strong>{event.email && <small>{event.email}</small>}</div>
          <time dateTime={event.created_at}>{date(event.created_at)}</time>
        </li>)}
      </ul>
    ) : <Empty>No account or invitation history.</Empty>)}
  </Modal>;
}

export function UserManagement() {
  const positions = useData<{ items: Position[] }>("/accounts/positions");
  const action = useAction();
  const [dialog, setDialog] = useState<{ operation: Operation; position: Position } | null>(null);
  const [history, setHistory] = useState<Position | null>(null);
  const [notice, setNotice] = useState("");
  const [validationError, setValidationError] = useState("");
  const openPositions = positions.data?.items.filter(available) ?? [];

  function open(position: Position, operation: Operation) {
    action.reset();
    setNotice("");
    setValidationError("");
    setDialog({ position, operation });
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dialog || action.isPending) return;
    const { operation, position } = dialog;
    const fields = new FormData(event.currentTarget);
    setValidationError("");
    let body: { email: string } | { assignment_id: string; target_slug?: string };
    if (operation === "invite") {
      const email = String(fields.get("email") || "").trim().toLowerCase();
      if (!mistEmail.test(email)) {
        setValidationError("Enter an individual @getmistified.com email address.");
        return;
      }
      body = { email };
    } else {
      if (!position.assignment_id) return;
      const target = String(fields.get("target_slug") || "");
      if (operation === "reassign" && !openPositions.some(item => item.slug === target)) {
        setValidationError("Choose an available department.");
        return;
      }
      body = { assignment_id: position.assignment_id, ...(operation === "reassign" ? { target_slug: target } : {}) };
    }
    try {
      await action.mutateAsync({ path: `/accounts/positions/${encodeURIComponent(position.slug)}/${operation}`, body });
      setNotice(operations[operation].notice);
      setDialog(null);
    } catch {}
  }

  const selected = dialog && operations[dialog.operation];
  return <div className="accounts-page">
    <Heading title="User Management" description="Manage individually invited MIST accounts and their department assignments." />
    <ErrorBox error={positions.error || (!dialog && action.error)} />
    {notice && <p className="account-notice" role="status">{notice}</p>}
    {positions.isPending && <Loading />}
    {positions.data && <div className="account-position-grid">
      {positions.data.items.map(position => (
        <section className="panel account-position" aria-label={position.name} key={position.slug}>
          <header>
            <h2><UserRound size={17} aria-hidden="true" />{position.name}</h2>
            <span className={`badge account-state ${position.state}`}>
              {position.state === "invited" && position.invitation_status === "expired" ? "Invitation expired" : positionLabels[position.state]}
            </span>
          </header>
          <dl className="account-position-details">
            <div><dt>{position.state === "revoked" ? "Last assigned email" : "Assigned email"}</dt>
              <dd>{position.email || "—"}</dd></div>
            <div><dt>Invitation</dt><dd>{position.invitation_status === "none" ? "—" : label(position.invitation_status)}</dd></div>
            {position.state === "invited" && position.invitation_status === "pending" && position.expires_at &&
              <div><dt>Expires</dt><dd><time dateTime={position.expires_at}>{date(position.expires_at)}</time></dd></div>}
            {position.state === "invited" && position.delivery_status &&
              <div><dt>Sandbox delivery</dt><dd>{position.delivery_status === "sent" ? "Captured" : label(position.delivery_status)}</dd></div>}
          </dl>
          <div className="account-position-actions">
            {available(position) && <button type="button" className="primary" disabled={action.isPending} onClick={() => open(position, "invite")}>
              <MailPlus size={16} aria-hidden="true" /> Invite
            </button>}
            {position.state === "invited" && <>
              <button type="button" disabled={action.isPending} onClick={() => open(position, "resend")}>Resend invitation</button>
              <button type="button" disabled={action.isPending} onClick={() => open(position, "revoke-invitation")}>Revoke invitation</button>
            </>}
            {position.state === "active" && <>
              <button type="button" disabled={action.isPending} onClick={() => open(position, "deactivate")}>Deactivate account</button>
              <button type="button" disabled={action.isPending || !openPositions.length} onClick={() => open(position, "reassign")}>Reassign</button>
            </>}
            {position.state === "deactivated" &&
              <button type="button" disabled={action.isPending} onClick={() => open(position, "reactivate")}>Reactivate account</button>}
            {["active", "deactivated"].includes(position.state) &&
              <button type="button" disabled={action.isPending} onClick={() => open(position, "revoke-account")}>Revoke account</button>}
            <button type="button" disabled={action.isPending} onClick={() => setHistory(position)}>
              <History size={16} aria-hidden="true" /> History
            </button>
          </div>
        </section>
      ))}
    </div>}
    {history && <PositionHistory position={history} onClose={() => setHistory(null)} />}
    {dialog && selected && <Modal title={`${selected.title} ${dialog.position.name}`} onClose={() => setDialog(null)} className="account-dialog">
      <form onSubmit={save} aria-busy={action.isPending}>
        {"detail" in selected && <p>{selected.detail}</p>}
        {dialog.operation === "invite" && <Field label="MIST email address">
          <input name="email" type="email" required maxLength={254} autoComplete="off" placeholder="name@getmistified.com" />
        </Field>}
        {dialog.operation === "reassign" && <Field label="Target department">
          <select name="target_slug" required defaultValue="">
            <option value="">Choose a department</option>
            {openPositions.map(position => <option key={position.slug} value={position.slug}>{position.name}</option>)}
          </select>
        </Field>}
        <ErrorBox error={validationError || action.error} />
        <div className="form-footer account-dialog-actions">
          <button type="button" disabled={action.isPending} onClick={() => setDialog(null)}>Cancel</button>
          <button type="submit" className={"destructive" in selected ? "account-danger" : "primary"} disabled={action.isPending}>
            {action.isPending ? "Saving…" : selected.submit}
          </button>
        </div>
      </form>
    </Modal>}
  </div>;
}

export type AccountAccessKind = "activate" | "forgot" | "reset";

const accessForms = {
  activate: {
    title: "Activate your account",
    description: "Choose a password for your invited MIST account.",
    submit: "Activate account",
    complete: "Your account is active. You can now sign in.",
    path: "/accounts/activate",
  },
  forgot: {
    title: "Forgot password",
    description: "Enter the MIST email address you use to sign in.",
    submit: "Request password reset",
    complete: "If an active account exists for that address, a password reset email has been requested.",
    path: "/accounts/password-reset/request",
  },
  reset: {
    title: "Reset your password",
    description: "Choose a new password for your MIST account.",
    submit: "Reset password",
    complete: "Your password is updated. Sign in with your new password.",
    path: "/accounts/password-reset/complete",
  },
};

export const accountAccessCopy = (kind: AccountAccessKind) => accessForms[kind];

export function AccountAccessForm({ kind }: { kind: AccountAccessKind }) {
  const config = accessForms[kind];
  const cache = useQueryClient();
  const mode = useData<{ managed: boolean }>("/auth/account-mode");
  const location = useLocation();
  const [token, setToken] = useState(() => kind === "forgot" ? null : new URLSearchParams(window.location.hash.slice(1)).get("token"));
  const inFlight = useRef(false);
  const [pending, setPending] = useState(false);
  const [complete, setComplete] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  useEffect(() => {
    if (kind === "forgot") return;
    const { hash, search, pathname } = window.location;
    if (hash) {
      setToken(new URLSearchParams(hash.slice(1)).get("token"));
      setComplete(false);
      setFailure(null);
    }
    if (hash || search) window.history.replaceState(window.history.state, "", pathname);
  }, [kind, location.hash, location.search]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || complete) return;
    const fields = new FormData(event.currentTarget);
    const password = String(fields.get("password") || "");
    if (kind !== "forgot" && (!token || password !== fields.get("confirm_password"))) {
      setFailure(token ? "Passwords do not match." : "Use the complete link from your email.");
      return;
    }
    inFlight.current = true;
    setPending(true);
    setFailure(null);
    try {
      // Keep credentials and link tokens out of the workspace mutation cache.
      await api(config.path, "POST", kind === "forgot" ? { email: fields.get("email") } : {
        token, password,
        ...(kind === "activate" ? { full_name: fields.get("full_name") } : {}),
      });
      setToken(null);
      setComplete(true);
      if (kind === "reset") void cache.invalidateQueries({ queryKey: ["/auth/me"] });
    } catch (error) {
      setFailure(error);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  return <div className="account-access">
    <ErrorBox error={mode.error} />
    {mode.isPending && <Loading />}
    {mode.data && !mode.data.managed && <Empty>This account link is unavailable.</Empty>}
    {mode.data?.managed && (complete ? <p className="account-notice" role="status">{config.complete}</p>
      : kind !== "forgot" && !token ? <ErrorBox error="Use the complete link from your email." />
      : <form onSubmit={submit} aria-busy={pending}>
        {kind === "forgot" ? <Field label="MIST email address">
          <input type="email" name="email" required maxLength={254} autoComplete="username" />
        </Field> : <>
          {kind === "activate" && <Field label="Full name">
            <input name="full_name" required maxLength={200} autoComplete="name" />
          </Field>}
          <Field label="New password">
            <input name="password" type="password" required minLength={12} maxLength={200} autoComplete="new-password"
              aria-describedby="account-password-guidance" />
          </Field>
          <p className="account-password-guidance" id="account-password-guidance">Use at least 12 characters.</p>
          <Field label="Confirm password">
            <input name="confirm_password" type="password" required minLength={12} maxLength={200} autoComplete="new-password" />
          </Field>
        </>}
        <ErrorBox error={failure} />
        <button className="primary login-submit" type="submit" disabled={pending}>{pending ? "Saving…" : config.submit}</button>
      </form>)}
    <p className="account-access-link"><Link to="/login">Back to sign in</Link></p>
  </div>;
}
