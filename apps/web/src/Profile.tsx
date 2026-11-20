import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { accountRoleLabel, useAction, type User } from "./api";
import { ErrorBox, Field, Heading, Modal, Panel } from "./ui";

export function Profile({ user }: { user: User }) {
  const action = useAction();
  const navigate = useNavigate();
  const location = useLocation();
  const [draft, setDraft] = useState<string | null>(null);
  const [confirmBack, setConfirmBack] = useState(false);
  const name = draft ?? user.full_name;
  const dirty = name.trim() !== user.full_name;

  function goBack() {
    if (location.state?.fromWorkspace && window.history.state?.idx > 0) {
      navigate(-1);
    } else {
      navigate("/dashboard", { replace: true });
    }
  }

  async function save(returnAfterSave = false) {
    try {
      await action.mutateAsync({ path: "/auth/me", method: "PATCH", body: { full_name: name.trim() } });
      setDraft(null);
      if (returnAfterSave) goBack();
    } catch {}
  }

  return <div className="profile-page">
    <Heading title="Profile">
      <button type="button" className="history-back" disabled={action.isPending} onClick={() => {
        if (dirty) setConfirmBack(true);
        else goBack();
      }}><ArrowLeft size={17} /> Back</button>
    </Heading>
    <Panel title="Account">
      <dl className="profile-account">
        <div><dt>Sign-in email</dt><dd>{user.email}</dd></div>
        <div><dt>Role</dt><dd>{accountRoleLabel(user)}</dd></div>
      </dl>
      <form onSubmit={event => { event.preventDefault(); void save(); }}>
        <Field label="Display name">
          <input required maxLength={200} autoComplete="name" value={name} disabled={action.isPending}
            onChange={event => { action.reset(); setDraft(event.target.value); }} />
        </Field>
        <ErrorBox error={action.error} />
        <button type="submit" className="primary" disabled={!dirty || !name.trim() || action.isPending}>Save</button>
        {action.isSuccess && !dirty && <p role="status" className="caption">Profile saved.</p>}
      </form>
    </Panel>
    {confirmBack && <Modal title="Save profile changes?" onClose={() => setConfirmBack(false)}>
      <p>Save your changes before going back?</p>
      <ErrorBox error={action.error} />
      <div className="form-footer">
        <button type="button" disabled={action.isPending} onClick={() => setConfirmBack(false)}>Keep editing</button>
        <button type="button" disabled={action.isPending} onClick={goBack}>Discard changes</button>
        <button type="button" className="primary" disabled={!name.trim() || action.isPending} onClick={() => void save(true)}>Save and go back</button>
      </div>
    </Modal>}
  </div>;
}
