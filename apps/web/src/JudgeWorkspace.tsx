import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowDownUp, ChevronLeft, ChevronRight, Mail, Phone, Search, SlidersHorizontal, X } from "lucide-react";
import { useAction, useData, type Page, type Row } from "./api";
import { Empty, ErrorBox, Field, Loading, Modal, Pagination } from "./ui";
import { useTournament } from "./CompetitionCoverage";

const calendarDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });

export function judgeDate(value?: string) {
  return value ? calendarDate.format(new Date(value)) : "—";
}

export function useJudgeWorkspace() {
  const [params, setParams] = useSearchParams();
  const tournament = useTournament();
  const year = tournament.year;
  function change(values: Record<string, string>, resetPage = true) {
    const next = new URLSearchParams(params);
    if (resetPage) next.delete("page");
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  }
  const competition = params.get("competition_id") || "";
  const category = params.get("category_slug") || "";
  const options = useData<{ competitions: Row[]; categories: Row[] }>(`/judge-directory/options?event_year=${year}`, year !== null);
  const page = Math.max(1, Math.floor(Number(params.get("page"))) || 1);
  function scope(keys: string[]) {
    const result = new URLSearchParams({ event_year: String(year), page: String(page) });
    keys.forEach(key => { if (params.has(key)) result.set(key, params.get(key)!); });
    return result;
  }
  function sort(key: string, defaultKey: string, defaultDirection: string) {
    const current = params.get("sort") || defaultKey;
    const direction = params.get("direction") || defaultDirection;
    change({ sort: key, direction: current === key && direction === "asc" ? "desc" : "asc" });
  }
  return { params, change, tournament, year, competition, category, options, page, scope, sort };
}

type Workspace = ReturnType<typeof useJudgeWorkspace>;

export function WorkspaceFilters({ workspace, recruitment = false, extra }: { workspace: Workspace; recruitment?: boolean; extra?: ReactNode }) {
  const [advanced, setAdvanced] = useState(false);
  const { params, change, competition, category, options } = workspace;
  return <>
    <div className="judge-filters">
      <label className="judge-search"><Search size={18} /><input type="search"
        aria-label={recruitment ? "Search candidates" : "Search judges by name or email"}
        placeholder={recruitment ? "Search candidates…" : "Search judges by name or email…"}
        value={params.get("q") || ""} onChange={e => change({ q: e.target.value })} /></label>
      <CompetitionSelect value={competition} items={options.data?.competitions.filter(row => !category || row.category_slug === category) || []}
        onChange={value => change({ competition_id: value })} />
      {recruitment && <select aria-label="Stage" value={params.get("stage") || ""} onChange={e => change({ stage: e.target.value })}>
        <option value="">All Stages</option><option value="prospect">Prospect</option>
        <option value="approved">Approved</option><option value="contacted">Contacted</option>
      </select>}
      <button type="button" className="judge-filter-toggle" aria-label="More filters" aria-expanded={advanced}
        onClick={() => setAdvanced(!advanced)}><SlidersHorizontal size={18} /></button>
    </div>
    {advanced && <div className="judge-extra-filters">
      <select aria-label="Category" value={category} onChange={e => change({ category_slug: e.target.value, competition_id: "" })}>
        <option value="">All Categories</option>{options.data?.categories.map(row => <option key={row.slug} value={row.slug}>{row.name}</option>)}
      </select>
      <button type="button" onClick={() => change({ q: "", competition_id: "", category_slug: "", stage: "" })}>Clear filters</button>
      {extra}
    </div>}
  </>;
}

export function JudgeDetailsEditor({ path, qualifications, notes, close }: {
  path: string; qualifications: string; notes: string; close: () => void;
}) {
  const action = useAction();
  return <Modal title="Edit qualifications and notes" onClose={close}>
    <form onSubmit={async event => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      try {
        await action.mutateAsync({ path, method: "PATCH", body: {
          qualifications: data.get("qualifications"), notes: data.get("notes"),
        } });
        close();
      } catch {}
    }}>
      <Field label="Qualifications"><textarea name="qualifications" maxLength={2000} defaultValue={qualifications} /></Field>
      <Field label="Notes"><textarea name="notes" maxLength={5000} defaultValue={notes} /></Field>
      <ErrorBox error={action.error} />
      <div className="form-footer"><button type="button" onClick={close}>Cancel</button>
        <button className="primary" disabled={action.isPending}>Save</button></div>
    </form>
  </Modal>;
}

export function CompetitionSelect({ value, items, onChange, required = false, id }: {
  value: string; items: Row[]; onChange: (value: string) => void; required?: boolean; id?: string;
}) {
  return <select id={id} aria-label={id ? undefined : "Competition"} required={required} value={value} onChange={e => onChange(e.target.value)}>
    <option value="">{required ? "Select a competition" : "All Competitions"}</option>
    {items.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}
  </select>;
}

export function JudgeMetric({ icon, label, value, color, onClick }: { icon: ReactNode; label: string; value: ReactNode; color?: string; onClick?: () => void }) {
  const contents = <><span className={`judge-metric-icon ${color || ""}`}>{icon}</span><span><strong>{value}</strong><small>{label}</small></span></>;
  return onClick ? <button type="button" className="judge-metric" onClick={onClick}>{contents}</button> : <div className="judge-metric">{contents}</div>;
}

export function SortColumn({ label, active, direction, onClick }: { label: string; active: boolean; direction: string; onClick: () => void }) {
  return <th scope="col" aria-sort={active ? direction === "desc" ? "descending" : "ascending" : undefined}>
    <button type="button" className="judge-sort" onClick={onClick}>{label} <ArrowDownUp size={13} /></button>
  </th>;
}

export function WorkspacePagination({ data, onPage, noun }: { data?: Page; onPage: (page: number) => void; noun: string }) {
  if (!data || !data.total) return null;
  const last = Math.ceil(data.total / data.page_size);
  const numbers = [...new Set([1, ...Array.from({ length: 5 }, (_, i) => Math.max(2, data.page - 2) + i), last])]
    .filter(number => number <= last).sort((a, b) => a - b);
  return <div className="judge-pagination">
    <span>Showing {(data.page - 1) * data.page_size + 1}–{Math.min(data.page * data.page_size, data.total)} of {data.total} {noun}</span>
    <nav aria-label="Pagination">
      <button type="button" aria-label="Previous page" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}><ChevronLeft size={17} /></button>
      {numbers.map((number, index) => <span key={number}>
        {index > 0 && number - numbers[index - 1] > 1 && <span className="judge-page-gap">…</span>}
        <button type="button" className={number === data.page ? "primary" : ""} aria-current={number === data.page ? "page" : undefined}
          aria-label={`Page ${number}`} onClick={() => onPage(number)}>{number}</button>
      </span>)}
      <button type="button" aria-label="Next page" disabled={data.page >= last} onClick={() => onPage(data.page + 1)}><ChevronRight size={17} /></button>
    </nav>
  </div>;
}

export function WorkspaceTabs({ labels, value, onChange, name }: { labels: [string, string][]; value: string; onChange: (value: string) => void; name: string }) {
  return <nav className="judge-tabs" aria-label={name}>{labels.map(([id, label]) =>
    <button type="button" aria-current={id === value ? "page" : undefined} onClick={() => onChange(id)} key={id}>{label}</button>)}</nav>;
}

export function JudgeSidePanel({ title, badge, children, close }: { title: string; badge: ReactNode; children: ReactNode; close: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [title]);
  return <aside className="judge-detail" aria-label={`Details for ${title}`} onKeyDown={e => { if (e.key === "Escape") close(); }}>
    <header><button type="button" className="judge-plain" aria-label="Back to results" onClick={close}><ChevronLeft size={20} /></button>
      <h2 ref={heading} tabIndex={-1}>{title}</h2>{badge}
      <button type="button" className="judge-plain judge-close" aria-label="Close details" onClick={close}><X size={19} /></button>
    </header>
    {children}
  </aside>;
}

export function ContactLines({ email, phone }: { email?: string; phone?: string }) {
  return <span className="judge-contact-lines">
    {email && <span><Mail size={15} />{email}</span>}{phone && <span><Phone size={15} />{phone}</span>}
    {!email && !phone && <span>—</span>}
  </span>;
}

export function JudgeIdentity({ row, children }: { row: Row; children?: ReactNode }) {
  return <div className="judge-identity"><div className="judge-avatar" aria-hidden="true">
    {String(row.name).trim().split(/\s+/).slice(0, 2).map(part => part[0]?.toUpperCase()).join("")}</div>
    <div><h3>{row.name}</h3><ContactLines email={row.email} phone={row.phone} /></div>
    {children}
  </div>;
}

export function JudgeConversations({ id }: { id: string }) {
  const [page, setPage] = useState(1);
  const query = useData<{ messages: Row[]; page: number; page_size: number; total: number }>(`/conversations/judges/${id}?page=${page}&page_size=10`);
  return <div className="judge-panel-body">
    <Link className="button" to={`/campaigns/conversations/judges/${id}`}>Open conversation</Link>
    <ErrorBox error={query.error} />
    {query.isPending ? <Loading /> : query.data?.messages.length ? <>
      {query.data.messages.map(row => <article className="judge-detail-card" key={row.id}>
        <strong>{row.subject}</strong><small>{row.direction === "incoming" ? "Received" : "Sandbox · " + row.status} · {judgeDate(row.created_at)}</small>
        <p className="message-body">{row.body}</p>
      </article>)}
      <Pagination data={{ ...query.data, items: query.data.messages }} onPage={setPage} />
    </> : <Empty>No messages yet.</Empty>}
  </div>;
}
