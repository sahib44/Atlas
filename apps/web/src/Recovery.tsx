import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, sessionQueryKey, useData } from "./api";
import { ErrorBox, Field, Loading, Modal, Panel } from "./ui";

const recoveryPath = "/accounts/recovery";
type Method = {
  id: string;
  kind: "email" | "sms";
  masked_destination: string;
  verified: boolean;
  preferred: boolean;
};
type Recovery = {
  methods: Method[];
  allow_mist_email: boolean;
  sms_available: boolean;
};
const methodActions = {
  resend: { title: "Resend verification", notice: "Verification instructions requested." },
  preferred: { title: "Make preferred", notice: "Preferred recovery method updated." },
  remove: { title: "Remove recovery method", notice: "Recovery method removed." },
};
type MethodAction = keyof typeof methodActions;
type Selection = { method: Method; action: MethodAction };

export function RecoveryMethods({ enrollment = false }: { enrollment?: boolean }) {
  const query = useData<Recovery>(recoveryPath);
  const cache = useQueryClient();
  const [adding, setAdding] = useState(enrollment);
  const [channel, setChannel] = useState<Method["kind"]>("email");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const recovery = query.data;
  const verifiedCount = recovery?.methods.filter(method => method.verified).length ?? 0;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setFailure(null);
    setNotice("");
    const fields = new FormData(event.currentTarget);
    const password = fields.get("password");
    try {
      if (selection) {
        await api(`${recoveryPath}/methods/${selection.method.id}/${selection.action}`, "POST", { password });
        setNotice(methodActions[selection.action].notice);
        setSelection(null);
      } else {
        await api(`${recoveryPath}/methods`, "POST", { kind: channel, destination: fields.get("destination"), password });
        setNotice("Verification instructions requested. Open the link to verify your recovery method.");
        setAdding(false);
      }
      await cache.invalidateQueries({ queryKey: [recoveryPath] });
    } catch (error) {
      setFailure(error);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  function openAction(method: Method, action: MethodAction) {
    setFailure(null);
    setNotice("");
    setSelection({ method, action });
  }

  const content = <>
    <ErrorBox error={query.error} />
    {query.isPending && <Loading />}
    {recovery && <>
      {!recovery.sms_available && <p className="recovery-hint">SMS recovery is unavailable until a provider is configured.</p>}
      {!!recovery.methods.length && <ul className="recovery-methods" aria-label="Registered recovery methods">
        {recovery.methods.map(method => <li key={method.id}>
          <div className="recovery-method-label">
            <strong>{method.masked_destination}</strong>
            <div className="recovery-actions">
              <span className={method.verified ? "badge" : "badge account-state invited"}>
                {method.verified ? "Verified" : "Unverified"}
              </span>
              {method.preferred && <span className="badge">Preferred</span>}
            </div>
          </div>
          <div className="recovery-actions">
            {!method.verified && <button type="button" onClick={() => openAction(method, "resend")} disabled={pending}>
              Resend verification
            </button>}
            {method.verified && !method.preferred && <button type="button" onClick={() => openAction(method, "preferred")} disabled={pending}>
              Make preferred
            </button>}
            <button type="button" onClick={() => openAction(method, "remove")}
              disabled={pending || (method.verified && verifiedCount === 1)}>Remove</button>
          </div>
        </li>)}
      </ul>}
      {!enrollment && verifiedCount === 1 && <p className="recovery-hint">Keep at least one verified recovery method.</p>}
      {notice && <p className="account-notice" role="status">{notice}</p>}
      {adding ? <form className="recovery-form" onSubmit={submit} aria-busy={pending}>
        <Field label="Recovery method">
          <select value={channel} onChange={event => setChannel(event.target.value === "sms" ? "sms" : "email")} disabled={pending}>
            <option value="email">{recovery.allow_mist_email ? "Email" : "Personal email"}</option>
            <option value="sms" disabled={!recovery.sms_available}>Phone (SMS)</option>
          </select>
        </Field>
        <Field label={channel === "sms" ? "Recovery phone number" : "Recovery email address"}>
          <input key={channel} name="destination" type={channel === "sms" ? "tel" : "email"} disabled={pending} required
            maxLength={channel === "sms" ? 32 : 254} autoComplete={channel === "sms" ? "tel" : "email"}
            aria-describedby="recovery-destination-hint" />
        </Field>
        <p className="recovery-hint" id="recovery-destination-hint">
          {channel === "sms" ? "Include your country code, such as +1."
            : recovery.allow_mist_email ? "Use a personal email or your individual MIST email."
              : "Use a personal email outside @getmistified.com."}
        </p>
        <Field label="Current Atlas password">
          <input name="password" type="password" autoComplete="current-password" disabled={pending} required />
        </Field>
        {!selection && <ErrorBox error={failure} />}
        <div className="recovery-actions">
          <button type="submit" className="primary" disabled={pending}>{pending ? "Requesting…" : "Send verification"}</button>
          {!enrollment && <button type="button" onClick={() => setAdding(false)} disabled={pending}>Cancel</button>}
        </div>
      </form> : <button type="button" onClick={() => { setFailure(null); setAdding(true); }} disabled={pending}>
        Add recovery method
      </button>}
    </>}
    {selection && <Modal title={methodActions[selection.action].title} onClose={() => setSelection(null)}>
      <p>{selection.method.masked_destination}</p>
      <form onSubmit={submit} aria-busy={pending}>
        <Field label="Current Atlas password">
          <input name="password" type="password" autoComplete="current-password" required disabled={pending} />
        </Field>
        <ErrorBox error={failure} />
        <div className="form-footer recovery-actions">
          <button type="button" onClick={() => setSelection(null)} disabled={pending}>Cancel</button>
          <button type="submit" className="primary" disabled={pending}>
            {pending ? "Saving…" : methodActions[selection.action].title}
          </button>
        </div>
      </form>
    </Modal>}
  </>;
  return enrollment ? <div className="account-access">{content}</div> : <Panel title="Recovery methods">{content}</Panel>;
}

export function VerifyRecoveryMethod({ token, enrollment }: { token: string | null; enrollment: boolean }) {
  const cache = useQueryClient();
  const navigate = useNavigate();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const inFlight = useRef(false);

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setFailure(null);
    try {
      await api(`${recoveryPath}/verify`, "POST", { token });
      await Promise.all([
        cache.invalidateQueries({ queryKey: sessionQueryKey }),
        cache.invalidateQueries({ queryKey: [recoveryPath] }),
      ]);
      navigate(enrollment ? "/dashboard" : "/profile", { replace: true });
    } catch (error) {
      setFailure(error);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  return <div className="account-access">
    {token ? <form onSubmit={verify} aria-busy={pending}>
      <ErrorBox error={failure} />
      <button type="submit" className="primary login-submit" disabled={pending}>
        {pending ? "Verifying…" : "Verify recovery method"}
      </button>
    </form> : <ErrorBox error="Open the complete link from your recovery email or text message." />}
    <p className="account-access-link">
      <Link to={enrollment ? "/recovery-setup" : "/profile"}>{enrollment ? "Back to recovery setup" : "Back to profile"}</Link>
    </p>
  </div>;
}
