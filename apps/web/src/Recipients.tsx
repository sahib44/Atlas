import { useState } from "react";
import { useData, Row } from "./api";
import { Mail, Users } from "lucide-react";
import { ErrorBox, Field } from "./ui";

export type EmailRecipient = { key: string; judge_id?: string; name: string; email: string; add_to_directory?: boolean };
export function RecipientChips({ recipients, onChange }: { recipients: EmailRecipient[]; onChange: (recipients: EmailRecipient[]) => void }) {
  return <div className="recipient-chips" aria-label="Selected recipients">{recipients.map(recipient => <span className="recipient-chip" key={recipient.key} title={recipient.email}>
    <span>{recipient.name || recipient.email}<small>{recipient.name && recipient.name !== recipient.email ? recipient.email : ""}</small></span>
    <button type="button" aria-label={`Remove ${recipient.name || recipient.email}`} onClick={() => onChange(recipients.filter(item => item.key !== recipient.key))}>×</button>
  </span>)}</div>;
}
export function RecipientPicker({ recipients, onChange }: { recipients: EmailRecipient[]; onChange: (recipients: EmailRecipient[]) => void }) {
  const [mode, setMode] = useState<"judges" | "one-off">("judges");
  const [search, setSearch] = useState("");
  const [address, setAddress] = useState("");
  const [name, setName] = useState("");
  const [save, setSave] = useState(false);
  const query = useData<Row[]>(`/recipient-search?q=${encodeURIComponent(search)}`, mode === "judges" && search.trim().length > 0);
  const email = address.trim().toLowerCase();
  const validEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  const hasAddress = (value: string) => recipients.some(recipient => recipient.email?.toLowerCase() === value.toLowerCase());
  const suggestions = (query.data || []).filter(item => !recipients.some(recipient => recipient.judge_id === item.id) && !hasAddress(item.email || ""));

  function addEmail() {
    if (!validEmail || hasAddress(email)) return;
    onChange([...recipients, { key: email, name: name.trim(), email, add_to_directory: save }]);
    setAddress("");
    setName("");
    setSave(false);
  }

  return <div className="recipient-picker">
    <div className="recipient-modes" role="group" aria-label="Recipient type">
      <button type="button" aria-pressed={mode === "judges"} onClick={() => setMode("judges")}><Users size={15} /> Select judges</button>
      <button type="button" aria-pressed={mode === "one-off"} onClick={() => setMode("one-off")}><Mail size={15} /> One-off email</button>
    </div>
    <div className="outreach-recipient-count"><Users size={16} /> {recipients.length} selected</div>
    <RecipientChips recipients={recipients} onChange={onChange} />
    {mode === "judges" ? <>
      <Field label="Search judges"><input autoComplete="off" value={search} onChange={event => setSearch(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter") event.preventDefault(); }} placeholder="Search by name or email" /></Field>
      {search.trim() && <div className="recipient-options">
        {suggestions.map(item => <button type="button" key={item.id} disabled={!item.email || item.do_not_contact} onClick={() => {
          onChange([...recipients, { key: item.id, judge_id: item.id, name: item.name, email: item.email }]);
          setSearch("");
        }}><strong>{item.name}</strong><small>{item.email || "No email address"}{item.do_not_contact ? " · Do not contact" : ""}</small></button>)}
        {query.isPending && <p role="status">Searching…</p>}
        {!suggestions.length && !query.isPending && !query.error && <p className="caption">No matching unselected judges.</p>}
        <ErrorBox error={query.error} />
      </div>}
    </> : <div className="recipient-direct">
      <Field label="Recipient email"><input type="text" inputMode="email" autoComplete="off" value={address} onChange={event => setAddress(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addEmail(); } }} /></Field>
      <Field label="Recipient name (optional)"><input value={name} onChange={event => setName(event.target.value)} /></Field>
      <label className="check-label"><input type="checkbox" checked={save} onChange={event => setSave(event.target.checked)} /> Save this person as a contact</label>
      <button type="button" disabled={!validEmail || hasAddress(email)} onClick={addEmail}>{validEmail ? `Add ${email}` : "Add recipient"}</button>
      {validEmail && hasAddress(email) && <small>This address is already selected.</small>}
    </div>}
  </div>;
}
