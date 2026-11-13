import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { CalendarCheck, Check, ChevronRight, CircleCheck, ClipboardCheck, ClipboardList, Ellipsis, FileText, GraduationCap, History, Mail, Pencil, Plus, Send, Trophy, UserRound, Users, UserRoundPlus } from "lucide-react";
import { useAction, useData, label, can, type Page, type Row } from "./api";
import { useUser } from "./App";
import { TournamentSelector } from "./CompetitionCoverage";
import { Empty, ErrorBox, Field, Loading, Modal } from "./ui";
import { CompetitionSelect, JudgeConversations, JudgeDetailsEditor, JudgeIdentity, JudgeMetric, JudgeSidePanel, SortColumn, WorkspaceFilters, WorkspacePagination, WorkspaceTabs, judgeDate, useJudgeWorkspace } from "./JudgeWorkspace";

const stages = [
  { id: "prospect", title: "Prospect", icon: UserRound },
  { id: "approved", title: "Approved", icon: ClipboardCheck },
  { id: "contacted", title: "Contacted", icon: Mail },
  { id: "confirmed", title: "Confirmed", icon: CircleCheck },
];
type Summary = { total: number; requests: number; contacted: number; confirmed: number; stages: Record<string, number> };
type Editor = { mode: "add" | "edit"; row?: Row };

function StagePill({ row }: { row: Row }) {
  const stage = stages.find(s => s.id === row.stage) || stages[0];
  const Icon = stage.icon;
  const archived = row.archive_status;
  return <span className={`judge-pill ${archived || stage.id}`}><Icon size={14} />{label(archived || stage.id)}</span>;
}

export function JudgeRecruitment() {
  const workspace = useJudgeWorkspace();
  const { params, change, year, tournament, competition, category, options } = workspace;
  const view = params.get("view") || "pipeline";
  const candidateId = params.get("candidate");
  const [editor, setEditor] = useState<Editor | null>(null);
  const summary = useData<Summary>(`/recruitment/summary?event_year=${year}`, year !== null);
  const list = useData<Page>(`/recruitment?${workspace.scope(["view", "q", "competition_id", "category_slug", "stage", "sort", "direction"])}`, year !== null);
  const detail = useData<Row>(`/recruitment/${candidateId}`, !!candidateId && year !== null);
  const current = detail.data?.event_year === year ? detail.data : undefined;
  const editing = editor || (params.get("add") === "1" ? { mode: "add" as const } : null);
  const rows = list.data?.items || [];
  const displayCompetitions = options.data?.competitions.filter(row => !category || row.category_slug === category) || [];
  const switchView = (next: string) => change({ view: next === "pipeline" ? "" : next, stage: "", candidate: "" });
  const select = (id: string) => change({ candidate: id }, false);
  const closeEditor = () => { setEditor(null); if (params.has("add")) change({ add: "" }, false); };
  const tabs: [string, string][] = [
    ["pipeline", "Pipeline"], ["requests", `Requests${summary.data ? ` (${summary.data.requests})` : ""}`],
    ["outreach", "Outreach"], ["notes", "Notes"], ["archive", "Archive"],
  ];

  return <div className={`judge-workspace recruitment-workspace ${candidateId ? "with-detail" : ""}`}>
    <section className="judge-results" aria-label="Judge recruitment">
      <div className="judge-heading">
        <h1>Judge Recruitment</h1>
        <div className="judge-heading-actions"><button className="primary" type="button" onClick={() => setEditor({ mode: "add" })} disabled={year === null}><Plus size={17} /> Add Candidate</button></div>
      </div>
      <div className="judge-metrics recruitment-metrics">
        <JudgeMetric icon={<Users />} value={summary.data?.total ?? "—"} label="Total Candidates" onClick={() => switchView("pipeline")} />
        <JudgeMetric icon={<UserRoundPlus />} value={summary.data?.requests ?? "—"} label="New Requests" onClick={() => switchView("requests")} />
        <JudgeMetric icon={<Mail />} value={summary.data?.contacted ?? "—"} label="Contacted" color="blue" onClick={() => switchView("outreach")} />
        <JudgeMetric icon={<CalendarCheck />} value={summary.data?.confirmed ?? "—"} label="Confirmed" color="green" />
      </div>
      <div className="recruitment-tab-row"><WorkspaceTabs labels={tabs} value={view} onChange={switchView} name="Recruitment views" />
        <TournamentSelector tournament={tournament} />
      </div>
      <div className="recruitment-pipeline" aria-label="Recruitment pipeline">
        {stages.map(({ id, title, icon: Icon }, index) => <div className="recruitment-step" key={id}>
          {id === "confirmed" ? <Link className={`recruitment-stage ${id}`} to={`/judges?event_year=${year}`}>
            <Icon size={24} /><span>{title}<strong>{summary.data?.confirmed ?? "—"}</strong></span></Link> :
            <button type="button" className={`recruitment-stage ${id}`} aria-pressed={params.get("stage") === id}
              onClick={() => change({ view: "", stage: params.get("stage") === id ? "" : id, candidate: "" })}>
              <Icon size={24} /><span>{title}<strong>{summary.data?.stages[id] ?? (summary.data ? 0 : "—")}</strong></span>
            </button>}
          {index < stages.length - 1 && <ChevronRight size={18} className="stage-arrow" aria-hidden="true" />}
        </div>)}
      </div>
      <WorkspaceFilters workspace={workspace} recruitment />
      <ErrorBox error={tournament.error || list.error || summary.error || options.error} />
      {year === null ? tournament.isPending ? <Loading /> : <Empty>No tournament is available.</Empty> :
        list.isPending ? <Loading /> : <div className="judge-table-wrap">
          <table className="judge-table">
            <thead><tr><th className="judge-check"><span className="visually-hidden">Selected</span></th>
              <SortColumn label="Name" active={params.get("sort") === "name"} direction={params.get("direction") || "asc"} onClick={() => workspace.sort("name", "updated", "desc")} />
              <th scope="col">Competitions</th><th scope="col">{view === "archive" ? "Outcome" : "Stage"}</th><th scope="col">Submitted By</th>
              <SortColumn label="Updated" active={(params.get("sort") || "updated") === "updated"} direction={params.get("direction") || "desc"} onClick={() => workspace.sort("updated", "updated", "desc")} />
              <th className="judge-more"><span className="visually-hidden">Actions</span></th></tr></thead>
            <tbody>{rows.map(row => <tr className={candidateId === row.id ? "judge-selected" : ""} key={row.id}>
              <td><input type="checkbox" checked={candidateId === row.id} aria-label={`Select ${row.name}`} onChange={() => select(candidateId === row.id ? "" : row.id)} /></td>
              <td><button className="judge-row-name" onClick={() => select(row.id)}>{row.name}</button></td>
              <td><span className="judge-competition-chip">{row.competition}</span></td><td><StagePill row={row} /></td>
              <td>{row.submitted_by}</td><td className="judge-date">{judgeDate(row.updated_at)}</td>
              <td><button type="button" className="judge-plain" aria-label={`Details for ${row.name}`} onClick={() => select(row.id)}><Ellipsis size={18} /></button></td>
            </tr>)}</tbody>
          </table>
          {!rows.length && <Empty>{view === "archive" ? "No archived candidates match these filters." : "No candidates match these filters."}</Empty>}
        </div>}
      <WorkspacePagination data={list.data} noun="candidates" onPage={page => change({ page: String(page) }, false)} />
    </section>
    {candidateId && (current ? <CandidatePanel key={current.id} row={current} close={() => select("")}
      onApprove={() => change({ view: "", stage: "approved", candidate: current.id })}
      onArchive={() => change({ view: "archive", stage: "", candidate: current.id })}
      onReopen={() => change({ view: "", stage: "prospect", candidate: current.id })}
      edit={mode => setEditor({ mode, row: current })} /> :
      <JudgeSidePanel title="Candidate details" badge={null} close={() => select("")}><div className="judge-panel-body">
        {detail.isPending ? <Loading /> : <ErrorBox error={detail.error || "This request is in another tournament."} />}
      </div></JudgeSidePanel>)}
    {editing && <CandidateEditor key={`${editing.mode}-${editing.row?.id || year}`} editor={editing} close={closeEditor}
      defaultCompetition={competition} competitions={editing.mode === "edit" ? options.data?.competitions || [] : displayCompetitions}
      onSaved={id => { setEditor(null); change({ add: "", candidate: id, view: "", stage: "" }, false); }} />}
  </div>;
}

function CandidateEditor({ editor, competitions, defaultCompetition, close, onSaved }: {
  editor: Editor; competitions: Row[]; defaultCompetition: string; close: () => void; onSaved: (id: string) => void;
}) {
  const initial = editor.row;
  const [competition, setCompetition] = useState(initial?.pilot_id || defaultCompetition);
  const [contactSearch, setContactSearch] = useState("");
  const [existing, setExisting] = useState<Row | null>(null);
  const [name, setName] = useState(initial?.name || "");
  const [email, setEmail] = useState(initial?.email || "");
  const [phone, setPhone] = useState(initial?.phone || "");
  const contactQuery = useData<Page>(`/judges?q=${encodeURIComponent(contactSearch)}&page_size=6`, contactSearch.trim().length >= 2 && !existing && editor.mode === "add");
  const action = useAction();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    try {
      const result = await action.mutateAsync({
        path: editor.mode === "edit" ? `/recruitment/${initial?.id}` : "/recruitment",
        method: editor.mode === "edit" ? "PATCH" : "POST",
        body: { competition_id: competition, name, email, phone, qualifications: fields.get("qualifications"),
          notes: fields.get("notes"), prior_involvement: fields.get("prior_involvement"), existing_judge_id: existing?.id || null },
      });
      onSaved(result.id);
    } catch {}
  }
  return <Modal title={editor.mode === "edit" ? "Edit judge request" : "Add candidate"} onClose={close}>
    <form onSubmit={submit} className="judge-edit-form">
      {editor.mode === "add" && <details className="pilot-existing"><summary>Use an existing contact</summary>
        <Field label="Find existing contact"><input type="search" value={contactSearch} onChange={e => { setContactSearch(e.target.value); setExisting(null); }} /></Field>
        {existing && <small>Using {existing.display_name}</small>}
        {!existing && contactQuery.data?.items.map(row => <button type="button" key={row.id} className="judge-contact-choice"
          onClick={() => { setExisting(row); setName(row.display_name); setEmail(row.primary_email || row.alternate_email || ""); setPhone(row.phone || ""); }}>
          {row.display_name} <small>{row.primary_email || row.alternate_email || row.phone}</small>
        </button>)}
      </details>}
      <Field label="Full name"><input required maxLength={400} value={name} onChange={e => setName(e.target.value)} /></Field>
      <div className="judge-form-grid"><Field label="Email"><input type="email" required maxLength={254} value={email} onChange={e => setEmail(e.target.value)} /></Field>
        <Field label="Phone (optional)"><input type="tel" maxLength={50} value={phone} onChange={e => setPhone(e.target.value)} /></Field></div>
      <Field label="Intended competition"><CompetitionSelect value={competition} items={competitions} onChange={setCompetition} required /></Field>
      <Field label="Qualifications"><textarea name="qualifications" required maxLength={2000} defaultValue={initial?.qualification_text || ""} /></Field>
      <Field label="Notes (optional)"><textarea name="notes" maxLength={5000} defaultValue={initial?.request_notes || ""} /></Field>
      <Field label="Prior MIST involvement (optional)"><input name="prior_involvement" maxLength={300} defaultValue={initial?.prior_involvement || ""} /></Field>
      <ErrorBox error={action.error} /><div className="form-footer"><button type="button" onClick={close}>Cancel</button>
        <button className="primary" disabled={action.isPending}>{editor.mode === "edit" ? "Save request" : "Submit for chair approval"}</button></div>
    </form>
  </Modal>;
}

function CandidatePanel({ row, close, edit, onApprove, onArchive, onReopen }: {
  row: Row; close: () => void; edit: (mode: Editor["mode"]) => void;
  onApprove: () => void; onArchive: () => void; onReopen: () => void;
}) {
  const user = useUser();
  const [tab, setTab] = useState("overview");
  const [reject, setReject] = useState(false);
  const [reopen, setReopen] = useState(false);
  const [declineDraft, setDeclineDraft] = useState(false);
  const [editingDetails, setEditingDetails] = useState(false);
  const action = useAction();
  const review = can(user, "recruitment_review") && row.review_status === "pending";
  const active = !row.archive_status && row.stage !== "confirmed";
  const approved = row.review_status === "approved";
  const archived = ["declined", "rejected", "withdrawn"].includes(row.archive_status);
  const canReopen = (archived && row.stage !== "confirmed") || (approved && active && can(user, "recruitment_review"));
  const canFollowUp = approved && !row.do_not_contact && (active || row.archive_status === "declined");
  const outreachPath = `/campaigns/new?competition_id=${row.pilot_id}&event_year=${row.event_year}${row.archive_status === "declined" ? "&allow_declined=1" : ""}`;
  const steps = [true, approved, row.stage === "contacted" || (row.stage === "confirmed" && !!row.last_contacted_at), row.stage === "confirmed"];
  const reviewDecision = async (decision: "approved" | "rejected") => {
    try {
      await action.mutateAsync({ path: `/recruitment/${row.id}/review`, body: { decision, version: row.updated_at } });
      setReject(false);
      if (decision === "approved") onApprove();
      else onArchive();
    } catch {}
  };
  return <JudgeSidePanel title={row.name} badge={<StagePill row={row} />} close={close}>
    <WorkspaceTabs name="Candidate details" value={tab} onChange={setTab} labels={[["overview", "Overview"], ["outreach", "Outreach"], ["notes", "Notes"], ["history", "History"]]} />
    {tab === "overview" && <div className="judge-panel-body">
      <JudgeIdentity row={row}><div className="judge-identity-meta">
        <span><Trophy size={16} />{row.competition}</span>
        {row.organization && <span><GraduationCap size={16} />{row.organization}</span>}
        {row.returning && <span><History size={16} />{row.prior_involvement || "Previous MIST involvement"}</span>}
      </div></JudgeIdentity>
      <section className="judge-detail-card candidate-progress"><h3>Recruitment Progress</h3><ol>
        {stages.map((stage, index) => <li className={steps[index] ? "complete" : ""} aria-current={row.stage === stage.id ? "step" : undefined} key={stage.id}>
          <span>{steps[index] && <Check size={15} />}</span>{stage.title}
        </li>)}
      </ol></section>
      <div className="candidate-panels">
        <section className="judge-detail-card"><h3>Judge Request Summary</h3>
          <ul className="candidate-summary">
            <li><FileText size={16} /><span className="candidate-summary-value"><strong>Requested by</strong> {row.submitted_by}</span></li>
            <li><CalendarCheck size={16} /><span className="candidate-summary-value"><strong>Requested on</strong> {judgeDate(row.created_at)}</span></li>
            <li><GraduationCap size={16} /><span className="candidate-summary-value"><strong>Qualifications</strong> {row.qualification_text || "—"}</span></li>
            <li><ClipboardList size={16} /><span className="candidate-summary-value"><strong>Notes</strong> {row.request_notes || row.contact_notes || "—"}</span></li>
          </ul>
        </section>
        <section className="judge-detail-card candidate-next"><h3>Next Steps</h3>
          {review ? <>
            <button className="primary" disabled={action.isPending} onClick={() => reviewDecision("approved")}><CircleCheck size={16} /> Approve request</button>
            <button onClick={() => edit("edit")}><Pencil size={15} /> Edit request</button>
            <button onClick={() => { action.reset(); setReject(true); }}>Reject request</button>
          </> : row.review_status === "pending" ? <p>Pending chair approval</p> : null}
          {canFollowUp && <Link className="button primary" to={outreachPath} state={{ judge_ids: [row.judge_id] }}><Send size={16} />
            {row.archive_status === "declined" ? "Manual follow-up" : row.last_contacted_at ? "Send Follow-up" : "Start Outreach"}</Link>}
          {approved && active && <>
            <Field label="Response"><select value={declineDraft ? "declined" : row.status} disabled={action.isPending} onChange={e => {
              const status = e.target.value;
              setDeclineDraft(status === "declined");
              if (status !== "declined" && status !== row.status) {
                action.mutate({ path: `/recruitment/${row.id}/response`, body: { status } });
              }
            }}>
              <option value="to_contact">To contact</option><option value="awaiting_response">Awaiting response</option>
              <option value="accepted">Accepted</option><option value="declined">Declined</option>
            </select></Field>
            {declineDraft ? <button type="button" disabled={action.isPending} onClick={async () => {
              try {
                await action.mutateAsync({ path: `/recruitment/${row.id}/response`, body: { status: "declined" } });
                setDeclineDraft(false);
                onArchive();
              } catch {}
            }}>Save decline</button> : row.status === "accepted" && <button disabled={action.isPending} onClick={async () => {
              try { await action.mutateAsync({ path: `/recruitment/${row.id}/confirm` }); close(); } catch {}
            }}><CircleCheck size={16} /> Confirm as Judge</button>}
          </>}
          {row.review_status === null && active && <button onClick={() => edit("add")}>Submit for review</button>}
          {row.stage === "confirmed" && <Link className="button" to={`/judges?event_year=${row.event_year}&q=${encodeURIComponent(row.name)}`}>View in Directory</Link>}
          {!active && row.archive_status && <p>{label(row.archive_status)}</p>}
          {canReopen && <button type="button" onClick={() => { action.reset(); setReopen(true); }}>
            {archived ? "Move back to Recruitment" : "Return to Prospect"}
          </button>}
          {row.do_not_contact && <p>Do not contact</p>}
          <ErrorBox error={action.error} />
        </section>
      </div>
    </div>}
    {tab === "outreach" && <JudgeConversations id={row.judge_id} />}
    {tab === "notes" && <div className="judge-panel-body"><section className="judge-detail-card"><h3>Request notes</h3>
      <p>{row.request_notes || row.contact_notes || "No notes recorded."}</p>{row.prior_involvement && <p>{row.prior_involvement}</p>}
      <button type="button" onClick={() => setEditingDetails(true)}><Pencil size={15} /> Edit qualifications and notes</button>
    </section></div>}
    {tab === "history" && <div className="judge-panel-body">{row.history?.length ? row.history.map((entry: Row) =>
      <article className="judge-detail-card" key={entry.id}><strong>{entry.summary}</strong><small>{entry.actor || "System"} · {judgeDate(entry.created_at)}</small></article>
    ) : <Empty>No request history recorded.</Empty>}</div>}
    {reject && <Modal title="Reject judge request" onClose={() => setReject(false)}>
      <p>Archive the request for {row.name} as Rejected?</p><ErrorBox error={action.error} />
      <div className="form-footer"><button onClick={() => setReject(false)}>Cancel</button>
        <button className="pilot-delete" disabled={action.isPending} onClick={() => reviewDecision("rejected")}>Confirm rejection</button></div>
    </Modal>}
    {reopen && <Modal title={archived ? "Move back to Recruitment" : "Return to Prospect"} onClose={() => setReopen(false)}>
      <p>{row.name} will return to Prospect and need chair approval before outreach. Their history will be preserved.</p>
      <ErrorBox error={action.error} />
      <div className="form-footer"><button type="button" onClick={() => setReopen(false)}>Cancel</button>
        <button type="button" className="primary" disabled={action.isPending} onClick={async () => {
          try {
            await action.mutateAsync({ path: `/recruitment/${row.id}/reopen` });
            setReopen(false);
            setDeclineDraft(false);
            onReopen();
          } catch {}
        }}>Reopen request</button></div>
    </Modal>}
    {editingDetails && <JudgeDetailsEditor path={`/recruitment/${row.id}/details`}
      qualifications={row.qualification_text || ""} notes={row.request_notes || ""} close={() => setEditingDetails(false)} />}
  </JudgeSidePanel>;
}
