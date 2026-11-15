import { useEffect, useId, useRef, useState } from "react";
import { Link, useParams, useSearchParams, useNavigate } from "react-router-dom";
import { Archive, ArrowLeft, Check, Clock, Forward, ListChecks, Lock, Mail, Play, Plus, Search, Send, ShieldCheck } from "lucide-react";
import { useData, useAction, Page, Row, can, label, date, requestId } from "./api";
import { useUser } from "./App";
import { Heading, Panel, Loading, ErrorBox, Empty, Badge, Pagination, Field, Modal } from "./ui";
import { WorkspacePagination } from "./JudgeWorkspace";
import "./tickets.css";

const ticketStatuses = ["new", "in_progress", "waiting_on_organizer", "waiting_on_requester", "resolved", "closed"];

const inboxViews = [
  ["all", "All"], ["open", "Open"], ["waiting", "Needs Reply"], ["overdue", "Overdue"],
] as const;

type TicketInboxPage = Page & { counts: Record<string, number> };

function ticketPath(id: string, params: URLSearchParams) {
  const search = params.toString();
  return `/tickets${id ? `/${id}` : ""}${search ? `?${search}` : ""}`;
}

const inboxDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const inboxTime = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

function activityDate(value: string) {
  const activity = new Date(value);
  const today = new Date();
  if (activity.toDateString() === today.toDateString()) return inboxTime.format(activity);
  today.setDate(today.getDate() - 1);
  return activity.toDateString() === today.toDateString() ? "Yesterday" : inboxDate.format(activity);
}

export function Tickets() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const filters = new URLSearchParams(params);
  filters.delete("draft");
  const inboxQuery = new URLSearchParams(filters);
  inboxQuery.set("page_size", "10");
  if (!inboxQuery.has("sort")) inboxQuery.set("sort", "activity");
  const query = useData<TicketInboxPage>(`/tickets?${inboxQuery}`);
  const action = useAction();
  const [selected, setSelected] = useState<string[]>([]);
  const [selecting, setSelecting] = useState(false);
  const [bulk, setBulk] = useState(false);
  const [status, setStatus] = useState("");
  const rows = query.data?.items ?? [];
  const currentId = id || rows[0]?.id || "";
  const listPath = ticketPath("", filters);
  const activeView = filters.get("view") || "all";
  const counts = query.data?.counts;

  function filter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (key !== "page") next.delete("page");
    value ? next.set(key, value) : next.delete(key);
    setParams(next, { replace: key === "q" });
    setSelected([]);
  }

  return <div className={`tickets-workspace${id ? " ticket-selected" : ""}`}>
    <header className="tickets-heading">
      <h1><Mail size={25} aria-hidden="true" /> Tickets</h1>
      <Link className="button primary" to="/tickets/new"><Plus size={16} /> New simulated ticket</Link>
    </header>
    <div className="ticket-layout">
      <section className="ticket-pane ticket-inbox" aria-label="Ticket inbox">
        <div className="ticket-inbox-controls">
          <nav className="ticket-views" aria-label="Inbox views">
            {inboxViews.map(([key, title]) => <button type="button" key={key} aria-pressed={activeView === key} onClick={() => filter("view", key === "all" ? "" : key)}>
              {title}{counts && <span>{counts[key]}</span>}
            </button>)}
          </nav>
          <label className="ticket-search"><Search size={17} aria-hidden="true" /><input type="search" aria-label="Search tickets" placeholder="Search tickets…" value={filters.get("q") || ""} onChange={event => filter("q", event.target.value)} /></label>
          <div className="ticket-filters">
            <select aria-label="Filter by status" value={filters.get("status") || ""} onChange={event => filter("status", event.target.value)}>
              <option value="">Status</option>{ticketStatuses.map(value => <option key={value} value={value}>{label(value)}</option>)}
            </select>
            <button type="button" className="ticket-select-mode" title="Select tickets" aria-label="Select tickets" aria-pressed={selecting} onClick={() => { setSelecting(!selecting); setSelected([]); }}><ListChecks size={17} /></button>
            <select aria-label="Sort tickets" value={filters.get("sort") || "activity"} onChange={event => filter("sort", event.target.value)}>
              <option value="activity">Sort: Last activity</option><option value="newest">Sort: Newest</option><option value="oldest">Sort: Oldest</option>
            </select>
          </div>
          {selecting && <div className="ticket-selection">
            <label><input type="checkbox" disabled={!rows.length} checked={!!rows.length && rows.every(row => selected.includes(row.id))} onChange={event => setSelected(event.target.checked ? rows.map(row => row.id) : [])} /> Select page</label>
            {!!selected.length && <button type="button" onClick={() => { setStatus(""); action.reset(); setBulk(true); }}>Update {selected.length} selected</button>}
          </div>}
          <ErrorBox error={query.error} />
        </div>
        <div className="ticket-inbox-scroll">
          {query.isPending ? <Loading /> : rows.length ? <ul className="ticket-inbox-list">
            {rows.map(row => <li key={row.id} className={`ticket-inbox-row${currentId === row.id ? " selected" : ""}`}>
              {selecting && <input type="checkbox" aria-label={`Select ${row.public_number}`} checked={selected.includes(row.id)} onChange={event => setSelected(event.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id))} />}
              <Link to={ticketPath(row.id, filters)} aria-current={currentId === row.id ? "true" : undefined}>
                <span className="ticket-row-top"><span>{row.unread && <span className="ticket-unread" aria-label="Unread" />}<strong>{row.public_number}</strong></span><time dateTime={row.last_message_at} title={date(row.last_message_at)}>{activityDate(row.last_message_at)}</time></span>
                <strong className="ticket-row-subject">{row.subject}</strong>
                <span className="ticket-row-summary"><span title={row.requester_email}>{row.requester_name}{row.preview && ` · ${row.preview}`}</span><Badge value={row.status} /></span>
              </Link>
            </li>)}
          </ul> : <Empty>No tickets match this view.</Empty>}
        </div>
        <WorkspacePagination data={query.data} onPage={page => filter("page", page === 1 ? "" : String(page))} noun="tickets" />
      </section>
      {currentId ? <TicketWorkspace key={currentId} id={currentId} listPath={listPath} /> : <section className="ticket-pane ticket-conversation ticket-empty" aria-label="Email conversation">
        <Mail size={29} /><Empty>Choose a ticket to view its email.</Empty>
      </section>}
    </div>
    {bulk && <Modal title="Update selected tickets" onClose={() => setBulk(false)}><form onSubmit={async event => {
      event.preventDefault();
      if (!confirm("Apply this status to all selected tickets?")) return;
      try {
        await action.mutateAsync({ path: "/tickets/bulk", body: { ids: selected, confirmed: true, status: { status } } });
        setBulk(false); setSelected([]);
      } catch {}
    }}>
      <Field label="Change status"><select required value={status} onChange={event => setStatus(event.target.value)}><option value="">Choose a status</option>{["in_progress", "closed"].map(value => <option key={value} value={value}>{label(value)}</option>)}</select></Field>
      <ErrorBox error={action.error} /><button className="primary" disabled={action.isPending}>Confirm bulk changes</button>
    </form></Modal>}
  </div>;
}

export function TicketNew() {
  const action = useAction();
  const navigate = useNavigate();
  return <>
    <Heading title="Simulate a ticket email" />
    <Panel title="Incoming message"><form onSubmit={async event => {
      event.preventDefault(); const fd = new FormData(event.currentTarget);
      try { const result = await action.mutateAsync({ path: "/tickets", body: { requester_name: fd.get("name"), requester_email: fd.get("email"), subject: fd.get("subject"), body: fd.get("body") } }); navigate(`/tickets/${result.id}`); } catch {}
    }}>
      <div className="form-grid"><Field label="Requester name"><input name="name" required /></Field><Field label="Requester email"><input type="email" name="email" required /></Field></div>
      <Field label="Subject"><input name="subject" required /></Field><Field label="Message body"><textarea name="body" rows={7} required /></Field>
      <ErrorBox error={action.error} /><button className="primary" disabled={action.isPending}>Create simulated ticket</button>
    </form></Panel>
  </>;
}

export type TicketComposerState = {
  mode: "messages" | "notes";
  reply: string;
  note: string;
  resolution_note: string;
  target_status: string;
};
const emptyComposer: TicketComposerState = { mode: "messages", reply: "", note: "", resolution_note: "", target_status: "" };

export function ReplyComposer({
  draft, onChange, changingStatus, pending, error, onSend, onRequestApproval,
  needsApproval = false, approved = false, approvalPending = false, valid = true,
  focusToken = 0, sectionRef, onSaveDraft, statusOnly = false,
}: {
  draft: TicketComposerState;
  onChange: (draft: TicketComposerState) => void;
  changingStatus: boolean;
  pending: boolean;
  error: unknown;
  onSend: () => Promise<void>;
  onRequestApproval?: () => Promise<void>;
  needsApproval?: boolean;
  approved?: boolean;
  approvalPending?: boolean;
  valid?: boolean;
  focusToken?: number;
  sectionRef?: React.RefObject<HTMLElement | null>;
  onSaveDraft?: () => void;
  statusOnly?: boolean;
}) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const resolution = useRef<HTMLTextAreaElement>(null);
  const bodyId = useId();
  useEffect(() => {
    if (!focusToken) return;
    sectionRef?.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const target = changingStatus && draft.target_status === "resolved" ? resolution.current : textarea.current;
    target?.focus({ preventScroll: true });
  }, [focusToken]);
  const publicRequired = changingStatus && draft.target_status === "waiting_on_requester";
  const replyMode = draft.mode === "messages";
  const noteRequired = draft.mode === "notes" && (!changingStatus || draft.target_status === "waiting_on_organizer");
  const resolving = changingStatus && draft.target_status === "resolved";
  const bodyLabel = replyMode ? "Public reply body" : "Private internal note";
  const bodyRequired = replyMode ? publicRequired || !changingStatus : noteRequired;
  const bodyQualifier = replyMode
    ? publicRequired ? " *" : resolving ? " (optional)" : ""
    : noteRequired ? " *" : " (optional)";
  const visibility = replyMode ? "Visible to requester" : "Team only";
  const approvalStatus = approved ? "Draft approved" : approvalPending ? "Review pending" : needsApproval ? "Approval required" : "";
  const buttonLabel = changingStatus
    ? draft.mode === "messages" && draft.reply.trim() ? "Update status and send reply" : draft.mode === "notes" && draft.note.trim() ? "Update status and save note" : "Update status"
    : draft.mode === "messages" ? "Send public sandbox reply" : "Save private internal note";
  const reviewable = !!(draft.mode === "messages" ? draft.reply.trim() || (resolving && draft.resolution_note.trim()) : draft.note.trim());
  return <section ref={sectionRef} className={`composer ${draft.mode === "notes" ? "private" : publicRequired ? "reply-required" : ""}`}>
    <div className="composer-topline">
      <div className="tabs">
        <button type="button" className={draft.mode === "messages" ? "active" : ""} aria-pressed={replyMode} disabled={pending || (changingStatus && draft.target_status === "waiting_on_organizer")} onClick={() => onChange({ ...draft, mode: "messages" })}><Send size={16} /> Public reply</button>
        <button type="button" className={draft.mode === "notes" ? "active" : ""} aria-pressed={!replyMode} disabled={pending || publicRequired || resolving} onClick={() => onChange({ ...draft, mode: "notes" })}><Lock size={16} /> Internal note</button>
      </div>
      {onRequestApproval && <button type="button" className="chair-approval-button" onClick={() => void onRequestApproval()} disabled={pending || !valid || !reviewable || approvalPending || approved}><ShieldCheck size={16} />{approved ? "Draft approved" : approvalPending ? "Awaiting chair approval" : "Request chair approval"}</button>}
    </div>
    <form onSubmit={event => { event.preventDefault(); void onSend(); }}>
      {resolving && <Field label="Resolution note *"><textarea aria-label="Resolution note" disabled={pending} ref={resolution} required rows={3} className="resolution-note-input" value={draft.resolution_note} onChange={event => onChange({ ...draft, resolution_note: event.target.value })} /></Field>}
      <div className="field ticket-composer-body">
        <div className="ticket-composer-label">
          <label htmlFor={bodyId}>{bodyLabel}{bodyQualifier}</label>
          <span id={`${bodyId}-visibility`}>{visibility}</span>
        </div>
        <span id={`${bodyId}-approval`} className="visually-hidden" role="status">{approvalStatus}</span>
        <textarea id={bodyId} ref={textarea} disabled={pending} aria-label={bodyLabel}
          aria-describedby={`${bodyId}-visibility ${bodyId}-approval`} required={bodyRequired} rows={4}
          placeholder={replyMode ? "Write a reply to the requester…" : "Write a private note…"}
          value={replyMode ? draft.reply : draft.note}
          onChange={event => onChange({ ...draft, [replyMode ? "reply" : "note"]: event.target.value })} />
      </div>
      <ErrorBox error={error} />
      <div className="ticket-composer-actions">
      {onSaveDraft && <button type="button" disabled={pending || !reviewable} onClick={onSaveDraft}>Save draft</button>}
      {!statusOnly && <button className={draft.mode === "messages" ? "primary" : ""} disabled={pending || !valid || (needsApproval && !approved)}>{draft.mode === "messages" ? <Send size={15} /> : <Lock size={15} />} {buttonLabel}</button>}
      </div>
    </form>
  </section>;
}

function TicketWorkspace({ id, listPath }: { id: string; listPath: string }) {
  const user = useUser();
  const [searchParams] = useSearchParams();
  const query = useData<Row>(`/tickets/${id}`);
  const [messagePage, setMessagePage] = useState<number>();
  const [notePage, setNotePage] = useState(1);
  const [activityPage, setActivityPage] = useState(1);
  const [detailTab, setDetailTab] = useState("details");
  const [showDetails, setShowDetails] = useState(false);
  const firstPage = useData<Page>(`/tickets/${id}/messages?page=1`);
  const lastPage = firstPage.data ? Math.max(1, Math.ceil(firstPage.data.total / firstPage.data.page_size)) : 1;
  const visiblePage = Math.min(messagePage ?? lastPage, lastPage);
  const laterPage = useData<Page>(`/tickets/${id}/messages?page=${visiblePage}`, visiblePage > 1);
  const messages = visiblePage === 1 ? firstPage : laterPage;
  const notes = useData<Page>(`/tickets/${id}/notes?page=${notePage}&message_page=${visiblePage}`, !!messages.data);
  const activity = useData<Page>(`/tickets/${id}/activity?page=${activityPage}`, detailTab === "activity");
  const thread = useRef<HTMLDivElement>(null);
  const reviews = useData<Page>(`/tickets/${id}/drafts`);
  const action = useAction();
  const storageKey = `mist-ticket-draft:${user?.id}:${id}`;
  const [draft, setDraft] = useState<TicketComposerState>(() => {
    try { return { ...emptyComposer, ...JSON.parse(sessionStorage.getItem(storageKey) || "{}") }; } catch { return { ...emptyComposer }; }
  });
  function persistDraft(value: TicketComposerState) {
    try { sessionStorage.setItem(storageKey, JSON.stringify(value)); return true; }
    catch { return false; }
  }
  useEffect(() => { persistDraft(draft); }, [draft, storageKey]);
  const [focusToken, setFocusToken] = useState(0);
  const composer = useRef<HTMLElement>(null);
  const approvalPanel = useRef<HTMLElement>(null);
  const [forwarding, setForwarding] = useState<Row | null>(null);
  const [notice, setNotice] = useState("");
  const [localError, setLocalError] = useState<unknown>();
  const sendRequest = useRef<{ signature: string; id: string } | null>(null);
  const reviewRequest = useRef<{ signature: string; id: string } | null>(null);
  const forwardRequest = useRef<{ signature: string; id: string } | null>(null);
  const isChair = can(user, "ticket_approve");
  const linkedDraft = searchParams.get("draft");
  const loadedPage = messages.data?.page;
  useEffect(() => {
    if (loadedPage && thread.current) thread.current.scrollTop = messagePage === undefined ? thread.current.scrollHeight : 0;
  }, [loadedPage, messagePage]);
  useEffect(() => {
    if (linkedDraft && reviews.data) {
      setDetailTab("details");
      approvalPanel.current?.focus({ preventScroll: true });
      approvalPanel.current?.scrollIntoView({ block: "nearest" });
    }
  }, [linkedDraft, !!reviews.data]);
  if (query.isPending) return <section className="ticket-pane ticket-conversation"><Loading /></section>;
  if (!query.data) return <section className="ticket-pane ticket-conversation"><ErrorBox error={query.error} /></section>;
  const ticket = query.data;
  const transitions: string[] = ticket.allowed_transitions;
  const changingStatus = transitions.includes(draft.target_status);
  const targetStatus = changingStatus ? draft.target_status : draft.mode === "messages" ? "waiting_on_requester" : "";
  const body = (draft.mode === "messages" ? draft.reply : draft.note).trim();
  const resolutionNote = changingStatus && draft.target_status === "resolved" ? draft.resolution_note.trim() : "";
  const matching = (review: Row) => review.created_by === user?.id && review.kind === draft.mode && review.body === body && review.target_status === targetStatus && review.resolution_note === resolutionNote && review.base_version === ticket.version;
  const approved = reviews.data?.items.find(review => review.status === "approved" && matching(review));
  const approvalPending = reviews.data?.items.some(review => review.status === "pending_approval" && matching(review)) || false;
  const includesReply = draft.mode === "messages" && !!draft.reply.trim();
  const needsApproval = includesReply && !isChair;
  const statusValid = !changingStatus ? false : draft.target_status === "waiting_on_requester" ? !!draft.reply.trim() && draft.mode === "messages" : draft.target_status === "waiting_on_organizer" ? !!draft.note.trim() && draft.mode === "notes" : draft.target_status === "resolved" ? !!resolutionNote : true;
  const normalValid = draft.mode === "notes" ? !!draft.note.trim() : !!draft.reply.trim() && ticket.status !== "closed";
  const valid = changingStatus ? statusValid : normalValid;
  function requestKey(ref: React.MutableRefObject<{ signature: string; id: string } | null>, payload: unknown) {
    const signature = JSON.stringify(payload);
    if (ref.current?.signature !== signature) ref.current = { signature, id: requestId() };
    return ref.current!.id;
  }
  function changeStatus(status: string) {
    setDraft(current => ({ ...current, target_status: status, mode: status === "waiting_on_organizer" ? "notes" : ["waiting_on_requester", "resolved"].includes(status) ? "messages" : current.mode }));
    setLocalError(undefined);
    if (["waiting_on_organizer", "waiting_on_requester", "resolved"].includes(status) && status !== ticket.status) {
      setShowDetails(false);
      setFocusToken(value => value + 1);
    }
  }
  async function requestApproval() {
    const payload = { kind: draft.mode, body, target_status: targetStatus, resolution_note: resolutionNote, expected_version: ticket.version };
    try {
      const review = await action.mutateAsync({ path: `/tickets/${id}/drafts`, body: { ...payload, request_id: requestKey(reviewRequest, payload) } });
      setDraft(current => ({ ...current, [draft.mode === "messages" ? "reply" : "note"]: review.body, resolution_note: resolutionNote ? review.resolution_note : current.resolution_note }));
      reviewRequest.current = null;
      setNotice("Draft sent to the chair for approval. Approval will not send the email automatically.");
      setLocalError(undefined);
    } catch (error) { setLocalError(error); }
  }
  async function submit(statusOnly?: string) {
    if (statusOnly && !transitions.includes(statusOnly)) return;
    if (!valid && !statusOnly) { setLocalError("Complete the required reply or note before updating this ticket."); return; }
    if (needsApproval && !approved && !statusOnly) { setLocalError("Request chair approval before sending this reply."); return; }
    try {
      if (changingStatus || statusOnly) {
        const payload = { status: statusOnly || draft.target_status, reply_body: !statusOnly && includesReply ? draft.reply.trim() : "", internal_note: !statusOnly && draft.mode === "notes" ? draft.note.trim() : "", resolution_note: statusOnly ? "" : resolutionNote, approval_id: statusOnly ? null : approved?.id || null, expected_version: ticket.version };
        await action.mutateAsync({ path: `/tickets/${id}/status`, body: { ...payload, request_id: requestKey(sendRequest, payload) } });
      } else {
        const payload = { body, approval_id: approved?.id || null };
        await action.mutateAsync({ path: `/tickets/${id}/${draft.mode}`, body: { ...payload, request_id: requestKey(sendRequest, payload) } });
      }
      sendRequest.current = null;
      setDraft(current => ({ ...current, reply: !statusOnly && current.mode === "messages" ? "" : current.reply, note: !statusOnly && current.mode === "notes" ? "" : current.note, resolution_note: statusOnly ? current.resolution_note : "", target_status: "" }));
      setNotePage(1); setMessagePage(undefined); setActivityPage(1); setLocalError(undefined);
      setNotice(statusOnly ? `Ticket status: ${label(statusOnly)}.` : changingStatus ? "Ticket status and its reply or note were saved together." : draft.mode === "notes" ? "Private note saved." : "Approved reply queued in the sandbox outbox.");
    } catch (error) { setLocalError(error); }
  }
  function loadApproved(review: Row) {
    setDraft({ mode: review.kind, reply: review.kind === "messages" ? review.body : draft.reply, note: review.kind === "notes" ? review.body : draft.note, resolution_note: review.resolution_note, target_status: review.target_status && review.target_status !== ticket.status ? review.target_status : "" });
    setFocusToken(value => value + 1);
  }
  const currentReviews = reviews.data?.items.filter(review =>
    ["pending_approval", "approved"].includes(review.status) && review.base_version === ticket.version) ?? [];
  const reviewHistory = reviews.data?.items.filter(review => !currentReviews.includes(review)) ?? [];
  const entries = [
    ...(messages.data?.items ?? []).map(row => ({ kind: "email", row })),
    ...(notes.data?.items ?? []).map(row => ({ kind: "note", row })),
  ].sort((a, b) => a.row.created_at.localeCompare(b.row.created_at));
  const simpleStatus = changingStatus && ["in_progress", "closed"].includes(targetStatus);

  function openForward(message: Row) {
    forwardRequest.current = null;
    action.reset();
    setNotice("");
    setForwarding(message);
  }

  function reviewCard(review: Row) {
    return <article id={`draft-${review.id}`} key={review.id} className={`ticket-review ${linkedDraft === review.id ? "highlighted-review" : ""}`}>
      <div className="ticket-review-meta"><strong>{review.author_name} · {review.kind === "notes" ? "Internal note" : "Public reply"}</strong><Badge value={review.status} /><time dateTime={review.created_at}>{date(review.created_at)}</time></div>
      {review.target_status && <small>Proposed status: {label(review.target_status)}</small>}
      {review.body && <p className="message-body">{review.body}</p>}
      {review.resolution_note && <div className="review-resolution"><strong>Private resolution note</strong><p className="message-body">{review.resolution_note}</p></div>}
      {currentReviews.includes(review) && review.status === "pending_approval" && isChair && <button className="primary" disabled={action.isPending} onClick={async () => {
        try {
          await action.mutateAsync({ path: `/tickets/${id}/drafts/${review.id}/approve` });
          setNotice("Draft approved. The organizer can now send or save it.");
        } catch {}
      }}>Approve draft</button>}
      {currentReviews.includes(review) && review.status === "approved" && review.created_by === user?.id &&
        <button type="button" onClick={() => loadApproved(review)}>Load approved draft</button>}
    </article>;
  }

  return <>
    <section className={`ticket-pane ticket-conversation${showDetails ? " ticket-conversation-hidden" : ""}`} aria-label="Email conversation">
      <header className="ticket-thread-header">
        <div className="ticket-compact-nav">
          <Link className="button" to={listPath}><ArrowLeft size={15} /> Inbox</Link>
          <button className="ticket-details-toggle" type="button" onClick={() => setShowDetails(true)} aria-expanded={showDetails} aria-controls="ticket-detail-panel">Details</button>
        </div>
        <div className="ticket-thread-title">
          <div><strong>{ticket.public_number}</strong><h2>{ticket.subject}</h2></div>
          <Badge value={ticket.status} />
        </div>
        <p>From: <span>{ticket.requester_email}</span></p>
      </header>
      <div className="ticket-thread-scroll" ref={thread} tabIndex={0} aria-label="Ticket messages">
        {notice && <div className="notice" role="status">{notice}</div>}
        <ErrorBox error={messages.error || notes.error} />
        {lastPage > 1 && <nav className="ticket-thread-pages" aria-label="Email pages">
          <button type="button" disabled={visiblePage <= 1} onClick={() => { setMessagePage(visiblePage - 1); setNotePage(1); }}>Older emails</button>
          <span>{visiblePage} / {lastPage}</span>
          <button type="button" disabled={visiblePage === lastPage} onClick={() => { setMessagePage(visiblePage === lastPage - 1 ? undefined : visiblePage + 1); setNotePage(1); }}>Newer emails</button>
          {visiblePage < lastPage && <button type="button" onClick={() => { setMessagePage(undefined); setNotePage(1); }}>Latest</button>}
        </nav>}
        {messages.isPending ? <Loading /> : <div className="ticket-thread-entries">
          {entries.map(({ kind, row }) => kind === "note" ? <article key={row.id} className="ticket-inline-note">
            <header><strong><Lock size={15} />{row.kind === "resolution" ? "Resolution note" : "Internal note"}{row.author_name && ` — ${row.author_name}`}</strong><time dateTime={row.created_at}>{date(row.created_at)}</time></header>
            <p className="message-body">{row.body}</p>
          </article> : <article key={row.id} className={`ticket-email ${row.message_type}`}>
            <header><span className="avatar" aria-hidden="true">{row.sender_name.trim().split(/\s+/).slice(0, 2).map((part: string) => part[0]).join("")}</span>
              <div><strong>{row.sender_name}</strong> <span>&lt;{row.sender_email}&gt;</span>
                <small>{row.message_type === "staff_reply" ? `To: ${ticket.requester_email}` : "To: Competitions"}</small>
              </div><time dateTime={row.created_at}>{date(row.created_at)}</time>
            </header>
            <p className="message-body">{row.body}</p>
            <div className="ticket-email-actions">
              {row.outbound_message_id && <span className="caption">Sandbox: {row.delivery_status ? label(row.delivery_status) : "Queued"}</span>}
              <button type="button" onClick={() => openForward(row)}><Forward size={14} /> Forward email</button>
            </div>
          </article>)}
        </div>}
        {notes.data && notes.data.total > notes.data.page_size && <div className="ticket-note-pagination"><span>Private notes</span><Pagination data={notes.data} onPage={setNotePage} /></div>}
        {(currentReviews.length > 0 || reviewHistory.length > 0) && <section className="ticket-approval-queue" ref={approvalPanel} tabIndex={-1} aria-label="Chair approval">
          <h3>Chair approval</h3>
          {currentReviews.map(reviewCard)}
          {reviewHistory.length > 0 && <details key={linkedDraft || "history"} open={reviewHistory.some(review => review.id === linkedDraft) || undefined}>
            <summary>Review history ({reviewHistory.length})</summary>{reviewHistory.map(reviewCard)}
          </details>}
        </section>}
      </div>
      <ReplyComposer draft={draft} onChange={setDraft} changingStatus={changingStatus} pending={action.isPending}
        error={simpleStatus ? null : localError || action.error} onSend={() => submit()}
        onRequestApproval={isChair || simpleStatus ? undefined : requestApproval}
        needsApproval={!simpleStatus && needsApproval} approved={!!approved} approvalPending={approvalPending}
        valid={valid && !simpleStatus} statusOnly={simpleStatus} focusToken={focusToken} sectionRef={composer}
        onSaveDraft={() => {
          if (persistDraft(draft)) { setLocalError(undefined); setNotice("Draft saved in this browser session."); }
          else setLocalError("This browser could not save the draft.");
        }} />
    </section>

    <aside id="ticket-detail-panel" className={`ticket-pane ticket-detail-panel${showDetails ? " ticket-detail-open" : ""}`} aria-label="Ticket details and activity">
      <nav className="ticket-detail-tabs" aria-label="Ticket information">
        <button type="button" aria-pressed={detailTab === "details"} onClick={() => setDetailTab("details")}>Details</button>
        <button type="button" aria-pressed={detailTab === "activity"} onClick={() => setDetailTab("activity")}>Activity</button>
        <button className="ticket-details-toggle" type="button" onClick={() => setShowDetails(false)} aria-label="Back to email"><ArrowLeft size={16} /></button>
      </nav>
      {detailTab === "activity" ? <div className="ticket-detail-body">
        <h3>Activity history</h3>
        <ErrorBox error={activity.error} />
        {activity.isPending ? <Loading /> : activity.data?.items.map(item => <div className="timeline-entry" key={item.id}><strong>{item.summary}</strong><time dateTime={item.created_at}>{date(item.created_at)}</time></div>)}
        <Pagination data={activity.data} onPage={setActivityPage} />
      </div> : <div className="ticket-detail-body">
        <h3>Ticket details</h3>
        <dl className="ticket-meta">
          <div><dt><label htmlFor="ticket-current-status">Status</label></dt><dd>
            <select id="ticket-current-status" aria-label="Ticket status" disabled={action.isPending} value={changingStatus ? draft.target_status : ticket.status} onChange={event => changeStatus(event.target.value)}>
              {[ticket.status, ...transitions].map(value => <option key={value} value={value}>{label(value)}</option>)}
            </select>
          </dd></div>
          <div><dt>Deadline</dt><dd>{date(ticket.auto_close_at)}</dd></div>
          <div><dt>Requester</dt><dd><strong>{ticket.requester_name}</strong><small>{ticket.requester_email}</small></dd></div>
          <div><dt>Created</dt><dd>{date(ticket.created_at)}</dd></div>
          <div><dt>Last activity</dt><dd>{date(ticket.last_message_at)}</dd></div>
        </dl>
        {changingStatus && !simpleStatus && <p className="ticket-status-hint">{targetStatus === "waiting_on_requester" ? isChair ? "A public reply is required." : "A chair-approved public reply is required." : targetStatus === "waiting_on_organizer" ? "Add a private note explaining the next step." : "Add a private resolution note. A public reply is optional."} <button type="button" onClick={() => changeStatus(ticket.status)}>Cancel</button></p>}
        <section className="ticket-context-actions">
          <h3>Actions</h3>
          {transitions.includes("in_progress") && <button type="button" disabled={action.isPending} onClick={() => void submit("in_progress")}><Play size={15} /> Mark In Progress</button>}
          {transitions.includes("resolved") && <button type="button" disabled={action.isPending} onClick={() => changeStatus("resolved")}><Check size={16} /> Resolve ticket</button>}
          {transitions.includes("closed") && <button type="button" disabled={action.isPending} onClick={() => { if (window.confirm("Close this ticket?")) void submit("closed"); }}><Archive size={15} /> Close ticket</button>}
          {can(user, "outbox") && <Link to={`/outbox?ticket_id=${id}`}><Mail size={15} /> View ticket outbox</Link>}
          <ErrorBox error={simpleStatus ? localError || action.error : null} />
        </section>
        <section className="ticket-chair-status">
          <h3>Chair approval</h3>
          <ErrorBox error={reviews.error} />
          {currentReviews.length ? <button type="button" onClick={() => {
            setShowDetails(false);
            requestAnimationFrame(() => { approvalPanel.current?.focus({ preventScroll: true }); approvalPanel.current?.scrollIntoView({ block: "nearest" }); });
          }}><ShieldCheck size={16} /> {currentReviews.some(review => review.status === "pending_approval") ? "Review pending" : "Draft approved"}</button>
            : <p><Clock size={15} /> No pending approvals</p>}
        </section>
      </div>}
    </aside>

    {forwarding && <Modal title="Forward email" className="ticket-forward-dialog" onClose={() => setForwarding(null)}>
      <form onSubmit={async event => {
        event.preventDefault();
        const fd = new FormData(event.currentTarget);
        const path = `/tickets/${id}/messages/${forwarding.id}/forward`;
        const payload = {
          recipient_email: String(fd.get("recipient_email") ?? "").trim().toLowerCase(),
          note: String(fd.get("note") ?? "").trim(),
        };
        try {
          await action.mutateAsync({ path, body: { ...payload, request_id: requestKey(forwardRequest, { path, ...payload }) } });
          setForwarding(null); setNotice("Forward queued in the sandbox outbox.");
        } catch {}
      }}>
        <Field label="Forward to"><input name="recipient_email" type="email" required /></Field>
        <Field label="Note (optional)"><textarea name="note" rows={3} /></Field>
        <section className="ticket-forward-preview" aria-label="Forwarded message preview">
          <strong>Quoted email</strong>
          <div>From: {forwarding.sender_name} &lt;{forwarding.sender_email}&gt;</div>
          <div>Subject: {ticket.subject}</div>
          <time dateTime={forwarding.created_at}>{date(forwarding.created_at)}</time>
          <p className="message-body">{forwarding.body}</p>
        </section>
        <p className="caption">The ticket stays in the Competitions inbox with its current status.</p>
        <ErrorBox error={action.error} />
        <div className="form-footer"><button type="button" onClick={() => setForwarding(null)}>Cancel</button><button className="primary" disabled={action.isPending}>Queue sandbox forward</button></div>
      </form>
    </Modal>}
  </>;
}
