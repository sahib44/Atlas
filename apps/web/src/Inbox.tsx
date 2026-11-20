import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Bell, ChevronRight, Mail, Plus } from "lucide-react";
import { useData, useAction, Page, Row, date, requestKey } from "./api";
import { Back, Badge, Empty, ErrorBox, Field, Heading, Loading, Modal, Pagination } from "./ui";

export function UnreadMarker({ count = 0 }: { count?: number }) {
  return count > 0 ? <span className="unread-marker" title={`${count} unread ${count === 1 ? "reply" : "replies"}`} aria-label={`${count} unread ${count === 1 ? "reply" : "replies"}`}>!</span> : null;
}
export function Notifications() {
  const query = useData<{ unread_count: number; items: Row[] }>("/notifications");
  const action = useAction();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  return <>
    <button className="notification-button" onClick={() => setOpen(true)} aria-label={`Notifications (${query.data?.unread_count || 0} unread)`}><Bell size={18} /> Notifications{(query.data?.unread_count || 0) > 0 && <span className="notification-count">{query.data?.unread_count}</span>}</button>
    {open && <Modal title="Notifications" onClose={() => setOpen(false)}><ErrorBox error={query.error || action.error} />
      <div className="notification-actions">
        <button type="button" disabled={!query.data?.unread_count || action.isPending}
          onClick={() => action.mutate({ path: "/notifications/read-all" })}>Mark all as read</button>
      </div>
      {query.isPending ? <Loading /> : !query.data?.items.length ? <Empty>No notifications yet.</Empty> : <div className="notification-list">{query.data.items.map(item => <button className={!item.read_at ? "notification-item unread" : "notification-item"} key={item.id} onClick={async () => {
        try { await action.mutateAsync({ path: `/notifications/${item.id}/read` }); setOpen(false); navigate(item.href); } catch {}
      }}><strong>{item.title}</strong><span>{item.body}</span><small>{date(item.created_at)}{!item.read_at ? " · Unread" : ""}</small></button>)}</div>}
    </Modal>}
  </>;
}

export function Conversations({ search, judgeId, page, onPage, returnTo }: { search: string; judgeId: string; page: number; onPage: (page: number) => void; returnTo: string }) {
  const params = new URLSearchParams({ q: search, page: String(page) });
  if (judgeId) params.set("judge_id", judgeId);
  const query = useData<Page>(`/conversations?${params}`);
  return <section aria-label="Judge conversations">
    <ErrorBox error={query.error} />
    {query.isPending ? <Loading /> : query.data?.items.length ? <div className="table-wrap outreach-table outreach-conversations">
      <table>
        <thead><tr><th>Judge</th><th>Replies</th><th>Last message</th></tr></thead>
        <tbody>{query.data.items.map(item => <tr key={item.id} className={item.unread_count ? "outreach-unread" : undefined}>
          <td><Link className="outreach-row-link" to={`/campaigns/conversations/judges/${item.id}`} state={{ workspaceReturn: returnTo }}>
            <span className="outreach-icon"><Mail size={17} aria-hidden="true" /></span>
            <span><strong>{item.name}</strong><small>{item.email}</small><small className="outreach-mobile-date">{date(item.last_message_at)}</small></span>
          </Link></td>
          <td>{item.unread_count ? <span className="badge pending_approval">{item.unread_count} unread</span> : "—"}</td>
          <td><span className="outreach-updated">{date(item.last_message_at)}</span><ChevronRight size={17} aria-hidden="true" className="outreach-chevron" /></td>
        </tr>)}</tbody>
      </table>
    </div> : query.data && <Empty>No conversations match this view.</Empty>}
    <Pagination data={query.data} onPage={onPage} />
  </section>;
}

export function JudgeConversation() {
  const { id = "" } = useParams();
  const location = useLocation();
  const workspaceReturn = location.state?.workspaceReturn || "/campaigns?view=conversations";
  const [page, setPage] = useState(1);
  const query = useData<Row>(`/conversations/judges/${id}?page=${page}`);
  const action = useAction();
  const marked = useRef("");
  const [openedUnread, setOpenedUnread] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!query.data?.unread_count || !query.data.read_through || marked.current === query.data.read_through) return;
    marked.current = query.data.read_through;
    setOpenedUnread(current => new Set([...current, ...query.data!.messages.filter((message: Row) => message.direction === "incoming" && !message.read_at).map((message: Row) => message.id)]));
    action.mutate({ path: `/conversations/judges/${id}/read`, body: { read_through: query.data.read_through } }, { onError: () => { marked.current = ""; } });
  }, [id, query.data]);
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorBox error={query.error} />;
  const data = query.data;
  return <>
    <Back to={workspaceReturn}>Judge conversations</Back>
    <Heading title={data.judge.name} description={[data.judge.email, data.judge.phone].filter(Boolean).join(" · ")}><Link className="button primary" to="/campaigns/new" state={{ judge_ids: [data.judge.id], workspaceReturn, composeReturn: location.pathname }}><Plus size={16} /> Compose email</Link></Heading>
    <ErrorBox error={action.error} />
    <div className="conversation-thread">{data.messages.map((message: Row) => <article key={message.id} className={`conversation-message ${message.direction}${openedUnread.has(message.id) ? " newly-opened" : ""}`}>
      <div className="message-heading"><strong>{message.direction === "incoming" ? data.judge.name : "MIST Atlas"}</strong><Badge value={message.status} /><small>{date(message.created_at)}</small></div>
      <small>{message.direction === "incoming" ? "From" : "To"}: {message.email}</small><h3>{message.subject}</h3><p className="message-body">{message.body}</p>{message.campaign_id && <Link to={`/campaigns/${message.campaign_id}`}>View campaign</Link>}
    </article>)}{!data.messages.length && <Empty>No email history yet.</Empty>}</div>
    <Pagination data={{ items: data.messages, total: data.total, page: data.page, page_size: data.page_size }} onPage={setPage} />
  </>;
}

export function SimulateIncoming({ judgeHref }: { judgeHref: (judgeId: string) => string }) {
  const [open, setOpen] = useState(false);
  const action = useAction();
  const navigate = useNavigate();
  const pendingRequest = useRef<{ signature: string; id: string } | null>(null);
  return <>
    <button onClick={() => { pendingRequest.current = null; action.reset(); setOpen(true); }}>Simulate incoming email</button>
    {open && <Modal title="Simulate incoming email" onClose={() => setOpen(false)}><p className="caption">Use this to test replies and ticket notifications. A real mailbox is not connected.</p><form onSubmit={async event => {
      event.preventDefault();
      if (action.isPending) return;
      const fd = new FormData(event.currentTarget);
      const payload = {
        sender_name: fd.get("sender_name"),
        sender_email: fd.get("sender_email"),
        subject: fd.get("subject"),
        body: fd.get("body"),
      };
      try {
        const result = await action.mutateAsync({ path: "/inbox/simulate", body: { ...payload, request_id: requestKey(pendingRequest, payload) } });
        setOpen(false);
        navigate(result.judge_id ? judgeHref(result.judge_id) : result.href);
      } catch {}
    }}>
      <Field label="Sender name"><input name="sender_name" /></Field><Field label="Sender email"><input name="sender_email" type="email" required /></Field><Field label="Subject"><input name="subject" required /></Field><Field label="Message"><textarea name="body" required rows={5} /></Field>
      <ErrorBox error={action.error} /><button className="primary" disabled={action.isPending}>Receive sandbox email</button>
    </form></Modal>}
  </>;
}
