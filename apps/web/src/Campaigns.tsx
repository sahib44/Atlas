import { useEffect, useRef, useState } from "react";
import {
  Link,
  useParams,
  useNavigate,
  useLocation,
  useSearchParams,
} from "react-router-dom";
import { Ban, ChevronRight, CircleAlert, Clock, FileText, Mail, Megaphone, MessageSquare, Paperclip, Plus, Search, Send, ShieldCheck, Users } from "lucide-react";
import { useData, useAction, api, Page, Row, can, label, date, requestId } from "./api";
import { useUser } from "./App";
import { RecipientPicker, EmailRecipient } from "./Recipients";
import { Conversations, SimulateIncoming } from "./Inbox";
import "./campaigns.css";
import {
  Heading,
  Panel,
  Loading,
  ErrorBox,
  Empty,
  Badge,
  Pagination,
  Field,
  Modal,
  Back,
  ConfirmButton,
} from "./ui";
const campaignStatuses = [
  "draft", "pending_approval", "approved", "queued", "sending", "completed", "partially_failed", "cancelled",
];

function campaignStatusLabel(status: string) {
  return status === "pending_approval" ? "Awaiting approval" : label(status);
}

function campaignWorkspacePath(params: URLSearchParams) {
  return params.size ? `/campaigns?${params}` : "/campaigns";
}

export function Campaigns() {
  const [params, setParams] = useSearchParams();
  const conversations = params.get("view") === "conversations" || (params.get("view") !== "campaigns" && params.has("judge"));
  const campaignFilters = new URLSearchParams();
  for (const key of ["q", "status", "page"]) {
    const value = params.get(key);
    if (value) campaignFilters.set(key, value);
  }
  const path = campaignWorkspacePath(params);
  const query = useData<Page>(`/campaigns?${campaignFilters}`, !conversations);
  const total = useData<Page>("/campaigns?page_size=1", !conversations);
  const pending = useData<Page>("/campaigns?status=pending_approval&page_size=1", !conversations);
  const drafts = useData<Page>("/campaigns?status=draft&page_size=1", !conversations);
  const searchKey = conversations ? "conversation_q" : "q";
  const pageKey = conversations ? "conversation_page" : "page";
  const allConversations = new URLSearchParams(params);
  allConversations.delete("judge");
  allConversations.delete("conversation_page");
  allConversations.set("view", "conversations");

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (key !== pageKey) next.delete(pageKey);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: key === searchKey });
  }

  function tabPath(view: string) {
    const next = new URLSearchParams(params);
    if (view === "campaigns" && !next.has("judge")) next.delete("view");
    else next.set("view", view);
    return campaignWorkspacePath(next);
  }

  return <div className="outreach">
    <header className="outreach-heading">
      <div className="outreach-title"><Mail aria-hidden="true" size={30} /><h1>Email Outreach</h1></div>
      <Link className="button primary" to="/campaigns/new" state={{ workspaceReturn: path }}>
        <Plus size={17} /> Create campaign
      </Link>
    </header>
    <nav className="outreach-tabs" aria-label="Email outreach views">
      <Link to={tabPath("campaigns")} aria-current={!conversations ? "page" : undefined}><Megaphone size={17} /> Campaigns</Link>
      <Link to={tabPath("conversations")} aria-current={conversations ? "page" : undefined}><MessageSquare size={17} /> Judge Conversations</Link>
    </nav>
    {!conversations && <div className="outreach-metrics" aria-label="Campaign metrics">
      {[
        { title: "Campaigns", query: total, icon: Megaphone },
        { title: "Awaiting approval", query: pending, icon: Clock },
        { title: "Drafts", query: drafts, icon: FileText },
      ].map(({ title, query: metric, icon: Icon }) => <div className="outreach-metric" key={title}>
        <span className="outreach-icon"><Icon size={23} aria-hidden="true" /></span>
        <span><strong>{metric.data?.total.toLocaleString() ?? "—"}</strong><span>{title}</span></span>
      </div>)}
    </div>}
    {!conversations && <ErrorBox error={total.error || pending.error || drafts.error} />}
    <div className="outreach-filters">
      <div className="outreach-search">
        <Search size={17} aria-hidden="true" />
        <input key={searchKey} type="search" aria-label={conversations ? "Search judge conversations" : "Search outreach campaigns"}
          placeholder={conversations ? "Search judge conversations…" : "Search campaigns…"} value={params.get(searchKey) || ""}
          onChange={event => updateFilter(searchKey, event.target.value)} />
      </div>
      {conversations ? <div className="actions">
        {params.has("judge") && <Link className="button" to={campaignWorkspacePath(allConversations)}>Show all conversations</Link>}
        <SimulateIncoming judgeHref={judgeId => {
          const next = new URLSearchParams(allConversations);
          next.delete("conversation_q");
          next.set("judge", judgeId);
          return campaignWorkspacePath(next);
        }} />
      </div> : <select aria-label="Campaign status" value={params.get("status") || ""} onChange={event => updateFilter("status", event.target.value)}>
        <option value="">All statuses</option>
        {campaignStatuses.map(status => <option key={status} value={status}>{campaignStatusLabel(status)}</option>)}
      </select>}
    </div>
    {conversations ? <Conversations search={params.get(searchKey) || ""} judgeId={params.get("judge") || ""}
      page={Math.max(1, Number(params.get("conversation_page")) || 1)} onPage={page => updateFilter("conversation_page", String(page))} returnTo={path} /> : <>
      <ErrorBox error={query.error} />
      {query.isPending ? <Loading /> : query.data?.items.length ? <div className="table-wrap outreach-table">
        <table>
          <thead><tr><th>Campaign</th><th>Status</th><th>Last updated</th></tr></thead>
          <tbody>{query.data.items.map(row => <tr key={row.id}>
            <td><Link className="outreach-row-link" to={`/campaigns/${row.id}`} state={{ workspaceReturn: path }}>
              <span className="outreach-icon"><Megaphone size={17} aria-hidden="true" /></span>
              <span><strong>{row.name}</strong><small>{row.subject_template}</small><small className="outreach-mobile-date">{date(row.updated_at || row.created_at)}</small></span>
            </Link></td>
            <td><span className={`badge ${row.status}`}>{campaignStatusLabel(row.status)}</span></td>
            <td><span className="outreach-updated">{date(row.updated_at || row.created_at)}</span><ChevronRight size={17} aria-hidden="true" className="outreach-chevron" /></td>
          </tr>)}</tbody>
        </table>
      </div> : query.data && <Empty>No campaigns match this view.</Empty>}
      <Pagination data={query.data} onPage={page => updateFilter("page", String(page))} />
    </>}
  </div>;
}

const judgeInvitation = {
  subject: "An invitation to judge {{event_name}}",
  body: "Hello {{first_name}},\n\nWe would love to invite you to judge {{category_name}} at {{event_name}} on {{event_date}}.\n\nPlease respond by {{response_deadline}}.\n\nThank you,\nThe MIST NJ team",
};

export function CampaignNew() {
  const [params] = useSearchParams();
  const pilotId = params.get("competition_id") || params.get("pilot_id");
  const pilot = useData<Row>(`/competitions/${pilotId}`, !!pilotId);
  const location = useLocation();
  const ids: string[] = location.state?.judge_ids || [];
  const workspaceReturn = location.state?.workspaceReturn || "/campaigns";
  const backTo = pilotId && pilot.data ? `/competitions/${pilot.data.category_slug}/${pilotId}` : location.state?.composeReturn || workspaceReturn;
  const backLabel = pilotId ? "Competition" : location.state?.composeReturn ? "Conversation" : "Campaigns";
  const [recipients, setRecipients] = useState<EmailRecipient[]>([]);
  const initial = useData<Row[]>(`/recipient-search?ids=${ids.join(",")}`, ids.length > 0);
  const [initialized, setInitialized] = useState(false);
  useEffect(() => {
    if (!initialized && initial.data) {
      setRecipients(initial.data.map(item => ({ key: item.id, judge_id: item.id, name: item.name, email: item.email })));
      setInitialized(true);
    }
  }, [initial.data, initialized]);
  const action = useAction();
  const navigate = useNavigate();
  const [excluded, setExcluded] = useState<any[]>([]);
  const [created, setCreated] = useState<Row | null>(null);
  const [fileError, setFileError] = useState<unknown>();
  const [template, setTemplate] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [fileCount, setFileCount] = useState(0);
  const [usedTemplate, setUsedTemplate] = useState(false);
  const detailPath = (id: string) => `/campaigns/${id}`;
  const detailState = { workspaceReturn };
  const draftRequest = useRef<{ signature: string; id: string } | null>(null);
  const submittingDraft = useRef(false);
  const [savingDraft, setSavingDraft] = useState(false);
  return (
    <div className="outreach outreach-composer">
      <Back to={backTo}>{backLabel}</Back>
      <header className="outreach-heading">
        <div>
          <h1>{pilotId ? `Compose for ${pilot.data?.name || "competition"}` : "Create Campaign"}</h1>
          {params.get("allow_declined") === "1" && <p>Manual follow-up to a declined candidate.</p>}
        </div>
        <ol className="outreach-compose-steps" aria-label="Campaign steps">
          {["Compose", "Review", "Submit"].map((step, index) => <li key={step} aria-current={index === 0 ? "step" : undefined}>
            <span>{index + 1}</span>{step}
          </li>)}
        </ol>
      </header>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (submittingDraft.current || created) return;
          const fd = new FormData(e.currentTarget);
          const payload = {
            name: fd.get("name"),
            subject_template: fd.get("subject"),
            body_template: fd.get("body"),
            judge_ids: recipients.filter(r => r.judge_id).map(r => r.judge_id),
            recipients: recipients.filter(r => !r.judge_id).map(r => ({ email: r.email, name: r.name, add_to_directory: !!r.add_to_directory })),
            pilot_competition_id: pilotId,
            event_year: params.get("event_year") ? Number(params.get("event_year")) : null,
            allow_declined_outreach: params.get("allow_declined") === "1",
            variables: {
              event_name: fd.get("event_name"),
              event_date: fd.get("event_date"),
              response_deadline: fd.get("response_deadline"),
            },
          };
          const signature = JSON.stringify(payload);
          if (draftRequest.current?.signature !== signature) {
            draftRequest.current = { signature, id: requestId() };
          }
          submittingDraft.current = true;
          setSavingDraft(true);
          try {
            const result = await action.mutateAsync({
              path: "/campaigns",
              body: { ...payload, request_id: draftRequest.current.id },
            });
            setExcluded(result.excluded);
            try {
              for (const file of fd.getAll("attachments")) {
                if (!(file instanceof File) || !file.name) continue;
                const upload = new FormData();
                upload.set("file", file);
                await api(`/campaigns/${result.id}/attachments`, "POST", upload);
              }
            } catch (cause) {
              setFileError(cause);
              setCreated(result);
              return;
            }
            if (result.excluded.length) setCreated(result);
            else navigate(detailPath(result.id), { state: detailState });
          } catch {} finally {
            submittingDraft.current = false;
            setSavingDraft(false);
          }
        }}
      >
        <div className="outreach-split">
          <div className="outreach-main">
            <Panel title="Recipients">
              <fieldset className="outreach-fieldset" disabled={ids.length > 0 && !initialized}>
                <legend className="outreach-sr-only">Campaign recipients</legend>
                <RecipientPicker recipients={recipients} onChange={setRecipients} />
              </fieldset>
              {ids.length > 0 && initial.isPending && <Loading />}
              <ErrorBox error={initial.error} />
            </Panel>
            <Panel title="Message">
              <Field label="Subject"><input name="subject" required value={subject} onChange={event => setSubject(event.target.value)} /></Field>
              <Field label="Email body"><textarea name="body" rows={7} required value={body} onChange={event => setBody(event.target.value)} /></Field>
              <div className="outreach-message-tools">
                <Field label="Message template">
                  <select value={template} onChange={event => setTemplate(event.target.value)}>
                    <option value="">Choose a template</option>
                    <option value="judge-invitation">Judge invitation</option>
                  </select>
                </Field>
                <Field label="Attachments (optional; up to five files, 5 MB each)">
                  <input name="attachments" type="file" multiple accept=".pdf,.docx,.png,.jpg,.jpeg,.txt,.csv"
                    onChange={event => setFileCount(event.target.files?.length ?? 0)} />
                </Field>
              </div>
              {template === "judge-invitation" && <details open className="pilot-existing">
                <summary>Judge invitation preview</summary>
                <p><strong>{judgeInvitation.subject}</strong></p>
                <p className="message-body">{judgeInvitation.body}</p>
                <button type="button" disabled={!!(subject.trim() || body.trim())} onClick={() => {
                  setSubject(judgeInvitation.subject);
                  setBody(judgeInvitation.body);
                  setUsedTemplate(true);
                }}>Use template in blank message</button>
              </details>}
              <details className="template-help">
                <summary>Personalization variables</summary>
                <div className="tags">
                  {["judge_name", "first_name", "category_name", "organization", "event_name", "event_date", "response_deadline"].map(variable =>
                    <code key={variable}>{`{{${variable}}}`}</code>)}
                </div>
              </details>
            </Panel>
          </div>
          <aside className="outreach-side">
            <Panel title="Campaign summary">
              <Field label="Campaign name"><input name="name" required /></Field>
              <ul className="outreach-summary-list">
                <li><Users size={16} /> {recipients.length} selected {recipients.length === 1 ? "recipient" : "recipients"}</li>
                {pilot.data?.name && <li><Megaphone size={16} /> {pilot.data.name}</li>}
                {usedTemplate && <li><FileText size={16} /> Invitation template inserted</li>}
                <li><Paperclip size={16} /> {fileCount} {fileCount === 1 ? "attachment" : "attachments"}</li>
              </ul>
            </Panel>
            <Panel title="Delivery">
              <p>Review each eligible recipient’s message after saving. Approval is required before queueing sandbox delivery.</p>
              <p>Duplicate addresses and do-not-contact recipients are excluded.</p>
            </Panel>
            <details className="panel outreach-event-fields" open={template === "judge-invitation" || undefined}>
              <summary>Event details</summary>
              <Field label="Event name"><input name="event_name" /></Field>
              <Field label="Event date"><input type="date" name="event_date" /></Field>
              <Field label="Response deadline"><input type="date" name="response_deadline" /></Field>
            </details>
          </aside>
        </div>
        <ErrorBox error={action.error} />
        <div className="form-footer">
          <Link className="button" to={backTo}>Cancel</Link>
          <button className="primary" disabled={savingDraft || !!created || !recipients.length || (ids.length > 0 && !initialized)}>
            <Mail size={16} /> {savingDraft ? "Saving draft…" : "Save draft & review"}
          </button>
        </div>
      </form>
      {created && (
        <Modal title={excluded.length ? "Review recipient exclusions" : "Attachment upload incomplete"}
          onClose={() => navigate(detailPath(created.id), { state: detailState })}>
          {!!excluded.length && <><p>{created.recipient_count} eligible recipients remain.</p>
            <ul>{excluded.map((recipient, index) => <li key={index}>{recipient.judge_name}: {recipient.reason}</li>)}</ul></>}
          {!!fileError && <><p>The draft was saved. Open it to retry the failed upload.</p><ErrorBox error={fileError} /></>}
          <Link className="button primary" to={detailPath(created.id)} state={detailState}>Continue to preview</Link>
        </Modal>
      )}
    </div>
  );
}

export function RecipientPreview({ recipients }: { recipients: Row[] }) {
  const [selected, setSelected] = useState<string>("");
  const recipient = recipients.find((r) => r.id === selected) || recipients[0];
  return recipient ? (
    <div className="preview-layout">
      <div className="recipient-list">
        {recipients.map((r) => (
          <button
            key={r.id}
            className={r.id === recipient.id ? "selected" : ""}
            onClick={() => setSelected(r.id)}
          >
            <Mail size={16} />
            <span>
              {r.recipient_name || r.recipient_email}
              <small>{r.recipient_email} · {label(r.status)}</small>
              {r.failure_reason && <small className="delivery-failure">{r.failure_reason}</small>}
            </span>
          </button>
        ))}
      </div>
      <article className="email-preview">
        <div className="email-meta">
          <span>TO</span>
          <strong>{recipient.recipient_email}</strong>
          <small>One recipient · No Cc or Bcc</small>
        </div>
        <h3>{recipient.rendered_subject}</h3>
        <p className="message-body">{recipient.rendered_body}</p>
      </article>
    </div>
  ) : (
    <Empty />
  );
}
export function CampaignDetail() {
  const { id } = useParams();
  const location = useLocation();
  const query = useData<Row>("/campaigns/" + id);
  const [page, setPage] = useState(1);
  const previews = useData<Page>(`/campaigns/${id}/preview?page=${page}`);
  const action = useAction();
  const [fileError, setFileError] = useState<unknown>();
  const user = useUser();
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorBox error={query.error} />;
  const campaign = query.data;
  const backTo = location.state?.workspaceReturn || "/campaigns";
  const outboxPath = "/outbox?campaign_id=" + id;
  const terminal = ["completed", "partially_failed", "cancelled"].includes(campaign.status);
  const canApprove = can(user, "approve");
  const counts = campaign.counts || {};
  const nextAction: Record<string, string> = {
    draft: "Review the individual messages, then submit this draft for approval.",
    pending_approval: canApprove ? "Review and approve the campaign to allow sandbox queueing." : "Waiting for the Competitions Chair to approve this campaign.",
    approved: "Queue individual messages for sandbox delivery.",
    queued: "Messages are waiting in the sandbox queue.",
    sending: "The sandbox worker is processing this campaign.",
    completed: "Recipient processing finished. Check sent and cancelled totals.",
    partially_failed: "Review failed recipients and their delivery errors.",
    cancelled: "This campaign was cancelled. Previously sent messages remain in the Outbox.",
  };

  function transition(step: string) {
    action.mutate({ path: `/campaigns/${id}/${step}` });
  }

  return <div className="outreach outreach-detail" id="campaign-overview">
    <Back to={backTo}>{backTo.includes("view=conversations") || backTo.includes("judge=") && !backTo.includes("view=campaigns") ? "Judge conversations" : "Campaigns"}</Back>
    <header className="outreach-heading outreach-detail-heading">
      <div>
        <h1>{campaign.name}</h1>
        <p>Created {date(campaign.created_at)}{campaign.event_year ? ` · ${campaign.event_year} tournament` : ""}</p>
      </div>
      <div className="outreach-detail-actions">
        <span className={`badge ${campaign.status}`}>{campaignStatusLabel(campaign.status)}</span>
        <div className="actions">
          {campaign.status === "draft" && <button className="primary" type="button" disabled={action.isPending}
            onClick={() => transition("submit")}>Submit for approval</button>}
          {campaign.status === "pending_approval" && canApprove && <ConfirmButton className="primary" disabled={action.isPending}
            question={`Approve ${campaign.recipient_count} individual sandbox emails?`} onConfirm={() => transition("approve")}>Approve campaign</ConfirmButton>}
          {campaign.status === "approved" && <ConfirmButton className="primary" disabled={action.isPending}
            question={`Queue ${campaign.recipient_count} individual sandbox messages?`} onConfirm={() => transition("queue")}>Queue sandbox delivery</ConfirmButton>}
          {!terminal && can(user, "campaign_cancel") && <ConfirmButton disabled={action.isPending}
            question="Cancel this campaign and stop all unsent messages?" onConfirm={() => transition("cancel")}>Cancel campaign</ConfirmButton>}
        </div>
      </div>
    </header>
    <nav className="outreach-tabs outreach-sections" aria-label="Campaign sections">
      <a href="#campaign-overview"><FileText size={16} /> Overview</a>
      <a href="#campaign-recipients"><Users size={16} /> Recipients & messages</a>
      <Link to={outboxPath} state={{ campaignName: campaign.name, workspaceReturn: backTo }}><Mail size={16} /> View sandbox outbox</Link>
      {can(user, "audit") && <Link to="/audit?q=campaign."><Clock size={16} /> Audit trail</Link>}
    </nav>
    <ErrorBox error={action.error} />
    <div className="outreach-split">
      <div className="outreach-main">
        <div className="outreach-metrics outreach-recipient-metrics" aria-label="Recipient delivery totals">
          {[
            { name: "Recipients", value: campaign.recipient_count, icon: Users },
            { name: "Sent", value: counts.sent ?? 0, icon: Send },
            { name: "Failed", value: counts.failed ?? 0, icon: CircleAlert },
            { name: "Cancelled", value: counts.cancelled ?? 0, icon: Ban },
          ].map(({ name, value, icon: Icon }) => <div className="outreach-metric" key={name}>
            <Icon size={22} aria-hidden="true" /><span><strong>{value}</strong><span>{name}</span></span>
          </div>)}
        </div>
        <div className={`outreach-next-action ${campaign.status === "pending_approval" ? "attention" : ""}`}>
          <Clock size={20} aria-hidden="true" />
          <div><strong>{terminal ? "Delivery outcome" : "Next action"}</strong><p>{nextAction[campaign.status]}</p></div>
        </div>
        <Panel title="Campaign details">
          <dl className="outreach-details-list">
            <div><dt>Subject template</dt><dd>{campaign.subject_template}</dd></div>
            {campaign.approved_at && <div><dt>Approved</dt><dd>{date(campaign.approved_at)}</dd></div>}
            <div><dt>Delivery</dt><dd>Sandbox only{counts.pending ? ` · ${counts.pending} pending` : ""}{counts.queued ? ` · ${counts.queued} queued` : ""}</dd></div>
            <div><dt>Attachments</dt><dd>
              {campaign.attachments?.length ? <ul className="attachment-list">{campaign.attachments.map((file: Row) => <li key={file.id}>
                <a href={`/api/campaigns/${id}/attachments/${file.id}`}>{file.filename}</a> <small>({Math.ceil(file.size / 1024)} KB)</small>
                {campaign.status === "draft" && can(user, "delete") && <button disabled={action.isPending}
                  onClick={() => action.mutate({ path: `/campaigns/${id}/attachments/${file.id}`, method: "DELETE" })}>Remove</button>}
              </li>)}</ul> : "None"}
            </dd></div>
          </dl>
          {campaign.status === "draft" && <Field label="Add file (PDF, DOCX, PNG, JPG, TXT, or CSV; 5 MB max)">
            <input type="file" accept=".pdf,.docx,.png,.jpg,.jpeg,.txt,.csv" onChange={async event => {
              const input = event.currentTarget;
              const file = input.files?.[0];
              if (!file) return;
              const body = new FormData();
              body.set("file", file);
              try {
                await api(`/campaigns/${id}/attachments`, "POST", body);
                setFileError(undefined);
                await query.refetch();
                input.value = "";
              } catch (cause) { setFileError(cause); }
            }} />
          </Field>}
          <ErrorBox error={fileError} />
        </Panel>
      </div>
      <aside className="outreach-side">
        <Panel title="Progress">
          <ol className="outreach-progress">
            <li aria-current={campaign.status === "draft" ? "step" : undefined}><strong>Draft saved</strong><small>{date(campaign.created_at)}</small></li>
            {campaign.approved_at && <li aria-current={campaign.status === "approved" ? "step" : undefined}><strong>Approved</strong><small>{date(campaign.approved_at)}</small></li>}
            {campaign.status !== "draft" && !(campaign.status === "approved" && campaign.approved_at) &&
              <li aria-current="step"><strong>{campaignStatusLabel(campaign.status)}</strong><small>Current status</small></li>}
          </ol>
        </Panel>
      </aside>
    </div>
    <section id="campaign-recipients" className="outreach-preview-section">
      <Panel title="Recipient-by-recipient preview">
        {!!campaign.failed_recipients?.length && <details className="outreach-failures">
          <summary>{campaign.failed_recipients.length} delivery {campaign.failed_recipients.length === 1 ? "failure" : "failures"}</summary>
          <ul>{campaign.failed_recipients.map((recipient: Row) => <li key={recipient.id}>
            {recipient.recipient_name || recipient.recipient_email}: {recipient.failure_reason}
          </li>)}</ul>
        </details>}
        <ErrorBox error={previews.error} />
        {previews.isPending ? <Loading /> : previews.data && <RecipientPreview recipients={previews.data.items} />}
        <Pagination data={previews.data} onPage={setPage} />
      </Panel>
    </section>
  </div>;
}

export function Outbox() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const campaignId = params.get("campaign_id");
  const query = useData<Page>("/outbox?" + params);
  const action = useAction();
  const user = useUser();
  const [selected, setSelected] = useState<Row | null>(null);
  const [forwarding, setForwarding] = useState(false);
  const [forwardNotice, setForwardNotice] = useState("");
  const forwardRequest = useRef<{ signature: string; id: string } | null>(null);
  const current = query.data?.items.find((r) => r.id === selected?.id);
  return (
    <>
      <Heading title="Sandbox outbox" />
      {campaignId && <div className="outreach-outbox-scope">
        <strong>{location.state?.campaignName || "Filtered to one campaign"}</strong>
        {can(user, "campaigns") && <Link to={`/campaigns/${campaignId}`} state={{ workspaceReturn: location.state?.workspaceReturn }}>Back to campaign</Link>}
        <button type="button" onClick={() => {
          const next = new URLSearchParams(params);
          next.delete("campaign_id");
          next.delete("page");
          setParams(next);
        }}>Clear campaign filter</button>
      </div>}
      <div className="review-callout outbox-reassurance">
        <ShieldCheck size={22} />
        <p>
          No email leaves this application. Delivery receipts below represent
          database captures by the mock provider.
        </p>
      </div>
      <div className="toolbar">
        <input
          aria-label="Search messages"
          placeholder="Search recipient or subject…"
          value={params.get("q") || ""}
          onChange={(e) => {
            params.set("q", e.target.value);
            params.delete("page");
            setParams(params);
          }}
        />
        <select
          aria-label="Message status"
          value={params.get("status") || ""}
          onChange={(e) => {
            params.set("status", e.target.value);
            params.delete("page");
            setParams(params);
          }}
        >
          <option value="">All statuses</option>
          {["queued", "sending", "sent", "failed", "cancelled"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <ErrorBox error={query.error || action.error} />
      {forwardNotice && <div role="status" className="notice">{forwardNotice}</div>}
      {query.isPending ? (
        <Loading />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Recipient & subject</th>
                <th>Related to</th>
                <th>Status</th>
                <th>Attempts</th>
                <th>Completed</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((r) => (
                <tr key={r.id}>
                  <td>
                    <strong>{r.recipient_email}</strong>
                    <small>{r.subject}</small>
                  </td>
                  <td>
                    {r.related_ticket_id ? (
                      <Link to={"/tickets/" + r.related_ticket_id}>
                        Ticket
                      </Link>
                    ) : (
                      "Campaign"
                    )}
                  </td>
                  <td>
                    <Badge value={r.status} />
                  </td>
                  <td>{r.attempt_count} / 3</td>
                  <td>{date(r.completed_at)}</td>
                  <td>
                    <button onClick={() => { setSelected(r); setForwarding(false); }}>Read message</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {query.data?.total === 0 && (
            <Empty>No sandbox messages match this view.</Empty>
          )}
          <Pagination
            data={query.data}
            onPage={(n) => {
              params.set("page", String(n));
              setParams(params);
            }}
          />
        </div>
      )}
      {current && (
        <Modal title="Sandbox message" onClose={() => setSelected(null)}>
          <div className="email-meta">
            <span>TO</span>
            <strong>{current.recipient_email}</strong>
            <Badge value={current.status} />
          </div>
          <h3>{current.subject}</h3>
          <p className="message-body">{current.body_text}</p>
          {!!current.attachments?.length && <div className="attachment-list"><strong>Attachments</strong>{current.attachments.map((file: Row) => <a key={file.id} href={`/api/outbox/${current.id}/attachments/${file.id}`}>{file.filename}</a>)}</div>}
          <dl>
            <div>
              <dt>Created</dt>
              <dd>{date(current.created_at)}</dd>
            </div>
            <div>
              <dt>Completed</dt>
              <dd>{date(current.completed_at)}</dd>
            </div>
            <div>
              <dt>Receipt</dt>
              <dd>{current.provider_message_id || "Pending"}</dd>
            </div>
            <div>
              <dt>Attempts</dt>
              <dd>{current.attempt_count}</dd>
            </div>
          </dl>
          {current.last_error && <ErrorBox error={current.last_error} />}
          <ErrorBox error={action.error} />
          {current.status === "sent" && can(user, "outbox") && <button onClick={() => {
            forwardRequest.current = null;
            setForwarding(!forwarding);
          }}>{forwarding ? "Cancel forward" : "Forward message"}</button>}
          {forwarding && <form onSubmit={async e => {
            e.preventDefault();
            const values = new FormData(e.currentTarget);
            const path = `/outbox/${current.id}/forward`;
            const payload = {
              recipient_email: String(values.get("recipient_email") ?? "").trim().toLowerCase(),
              note: String(values.get("note") ?? "").trim(),
            };
            const signature = JSON.stringify({ path, ...payload });
            if (forwardRequest.current?.signature !== signature) {
              forwardRequest.current = { signature, id: requestId() };
            }
            try {
              await action.mutateAsync({ path, body: { ...payload, request_id: forwardRequest.current.id } });
              setForwardNotice("Forward queued in the sandbox outbox.");
              setForwarding(false);
              setSelected(null);
            } catch {}
          }}>
            <Field label="Forward to"><input name="recipient_email" type="email" required placeholder="registration@example.com" /></Field>
            <Field label="Note (optional)"><textarea name="note" rows={3} /></Field>
            <p className="caption">The original message and its attachments will be included. Delivery stays in the sandbox.</p>
            <button className="primary" disabled={action.isPending}>Queue sandbox forward</button>
          </form>}
          {can(user, "jobs") &&
            ["queued", "failed"].includes(current.status) && (
              <div className="actions">
                <button
                  onClick={() =>
                    action.mutate({
                      path: "/outbox/" + current.id + "/failure",
                      body: { enabled: !current.simulate_failure },
                    })
                  }
                >
                  {current.simulate_failure ? "Disable" : "Enable"} failure
                  simulation
                </button>
                {current.status === "failed" && (
                  <button
                    onClick={() =>
                      action.mutate({
                        path: "/outbox/" + current.id + "/retry",
                      })
                    }
                  >
                    Retry failed message
                  </button>
                )}
              </div>
            )}
        </Modal>
      )}
    </>
  );
}
