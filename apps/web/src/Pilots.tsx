import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { FileText, Mail, Plus, Trash2, Upload, Users } from "lucide-react";
import { useAction, useData, Page, Row, can, date, label } from "./api";
import { useUser } from "./App";
import { UnreadMarker } from "./Inbox";
import { Back, Empty, ErrorBox, Field, Heading, Loading, Modal, Pagination, Panel } from "./ui";
import { Coverage, JudgeCoverage, TournamentSelector, tournamentPath, useTournament } from "./CompetitionCoverage";
import { AssignmentCandidate, OfficialJudgeSlots, OfficialSlot } from "./OfficialJudgeSlots";

const stages = [
  ["", "All judges"],
  ["to_contact", "To contact"],
  ["awaiting_response", "Awaiting response"],
  ["accepted", "Accepted"],
  ["declined", "Declined"],
] as const;

type Category = Coverage & { slug: string; name: string; competition_count: number; unread_count: number };
type CompetitionDetail = Row & Coverage & { event_year: number; official_assignments: OfficialSlot[] };

export function Competitions() {
  const tournament = useTournament();
  const categories = useData<Category[]>(tournamentPath("/competitions/categories", tournament.year), !tournament.isPending && !tournament.error);
  const user = useUser();
  return <>
    <Heading eyebrow="JUDGE HANDLING" title="Competitions">
      <TournamentSelector tournament={tournament} />
      {(can(user, "judges") || can(user, "audit")) && <Link className="button" to="/competitions/deletions">Deleted pilots</Link>}
    </Heading>
    <ErrorBox error={tournament.error || categories.error} />
    {!tournament.error && (categories.isPending ? <Loading /> : <div className="pilot-grid">
      {categories.data?.map((category) => <Link className="pilot-card" to={tournamentPath(`/competitions/${category.slug}`, tournament.year)} key={category.slug}>
        <span className="pilot-card-icon"><Users size={20} /></span>
        <strong>{category.name} <UnreadMarker count={category.unread_count} /></strong>
        <JudgeCoverage name={category.name} confirmed_count={category.confirmed_count} required_count={category.required_count} />
        <small>{category.competition_count} active {category.competition_count === 1 ? "competition" : "competitions"}</small>
      </Link>)}
    </div>)}
  </>;
}

export function DeletedCompetitions() {
  const [page, setPage] = useState(1);
  const query = useData<Page>(`/competitions/deletions?page=${page}`);
  return <>
    <Back to="/competitions">Competitions</Back>
    <Heading title="Deleted pilots" />
    <ErrorBox error={query.error} />
    {query.isPending ? <Loading /> : <div className="table-wrap"><table><thead><tr><th>Pilot</th><th>Category</th><th>Year</th><th>Deleted by</th><th>Deleted</th></tr></thead><tbody>
      {query.data?.items.map((row: Row) => <tr key={row.id}><td>{row.name}</td><td>{row.category_name}</td><td>{row.event_year}</td><td>{row.deleted_by_name}</td><td>{date(row.deleted_at)}</td></tr>)}
    </tbody></table>{query.data?.total === 0 && <Empty>No pilots have been deleted.</Empty>}<Pagination data={query.data} onPage={setPage} /></div>}
  </>;
}

export function CompetitionCategory() {
  const { category = "" } = useParams();
  const tournament = useTournament();
  const [params, setParams] = useSearchParams();
  const pageNumber = Math.max(1, Math.floor(Number(params.get("page")) || 1));
  const categories = useData<Category[]>(tournamentPath("/competitions/categories", tournament.year), !tournament.isPending && !tournament.error);
  const categoryName = categories.data?.find((item) => item.slug === category)?.name || "Competitions";
  const scope = new URLSearchParams({ category_slug: category, page: String(pageNumber) });
  if (tournament.year !== null) scope.set("event_year", String(tournament.year));
  const query = useData<Page>(`/competitions?${scope}`, !tournament.isPending && !tournament.error);
  const action = useAction();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const existing = useData<Page>("/judges?page_size=100", creating);
  return (
    <>
      <Back to={tournamentPath("/competitions", tournament.year)}>Competitions</Back>
      <Heading eyebrow="COMPETITIONS" title={categoryName}>
        <TournamentSelector tournament={tournament} />
        <button className="primary" onClick={() => { setChosen([]); setCreating(true); }}>
          <Plus size={16} /> Add pilot competition
        </button>
      </Heading>
      <ErrorBox error={tournament.error || query.error} />
      {!tournament.error && (query.isPending ? <Loading /> : query.data?.items.length ? (
        <div className="pilot-grid">
          {query.data.items.map((pilot) => (
            <Link className="pilot-card" to={`/competitions/${category}/${pilot.id}`} key={pilot.id}>
              <span className="pilot-card-icon"><Users size={20} /></span>
              {pilot.is_pilot && <span className="pilot-card-year">PILOT · {pilot.event_year}</span>}
              <strong>{pilot.name} <UnreadMarker count={pilot.unread_count} /></strong>
              <JudgeCoverage name={pilot.name} confirmed_count={pilot.confirmed_count} required_count={pilot.required_count} />
              <small>{Object.values(pilot.counts || {}).reduce((sum: number, count) => sum + Number(count), 0)} recruitment records · {pilot.documents?.length || 0} documents</small>
            </Link>
          ))}
        </div>
      ) : query.data && <Empty>No active competitions in this category for {tournament.year ?? "this tournament"}.</Empty>)}
      <Pagination data={query.data} onPage={number => {
        const next = new URLSearchParams(params);
        next.set("page", String(number));
        setParams(next);
      }} />
      {creating && (
        <Modal title="Add pilot competition" onClose={() => setCreating(false)}>
          <form onSubmit={async (event) => {
            event.preventDefault();
            const body = new FormData(event.currentTarget);
            body.set("judge_ids", JSON.stringify(chosen));
            body.set("category_slug", category);
            try {
              const result = await action.mutateAsync({ path: "/competitions", body });
              navigate(`/competitions/${category}/${result.id}`);
            } catch {}
          }}>
            <Field label="Competition name"><input name="name" required maxLength={200} /></Field>
            <Field label="Event year"><input name="event_year" type="number" min="2000" max="2200" required /></Field>
            <Field label="Rules (optional PDF or DOCX)"><input name="rules" type="file" accept=".pdf,.docx" /></Field>
            <Field label="Procedures (optional PDF or DOCX)"><input name="procedures" type="file" accept=".pdf,.docx" /></Field>
            <details className="pilot-existing">
              <summary>Add existing judges (optional) · {chosen.length} selected</summary>
              <div className="pilot-existing-list">
                {existing.data?.items.map((judge) => (
                  <label key={judge.id}>
                    <input type="checkbox" checked={chosen.includes(judge.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, judge.id] : chosen.filter((id) => id !== judge.id))} />
                    <span>{judge.display_name}<small>{judge.primary_email || judge.phone || "No contact method"}</small></span>
                  </label>
                ))}
              </div>
            </details>
            <p className="caption">Documents stay in the local database. No email is sent when you create a pilot.</p>
            <ErrorBox error={action.error} />
            <div className="form-footer"><button type="button" onClick={() => setCreating(false)}>Cancel</button><button className="primary" disabled={action.isPending}>Create pilot</button></div>
          </form>
        </Modal>
      )}
    </>
  );
}

export function PilotDetail() {
  const { id = "" } = useParams();
  return <PilotWorkspace id={id} key={id} />;
}

function PilotWorkspace({ id }: { id: string }) {
  const user = useUser();
  const pilot = useData<CompetitionDetail>(`/competitions/${id}`);
  const action = useAction();
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [overdue, setOverdue] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [declineDraft, setDeclineDraft] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<AssignmentCandidate | null>(null);
  const [documentKind, setDocumentKind] = useState("");
  const [deleteStep, setDeleteStep] = useState(0);
  const [deleteName, setDeleteName] = useState("");
  const [localError, setLocalError] = useState<unknown>();
  const params = new URLSearchParams({ page: String(page), page_size: "100" });
  if (status) params.set("status", status);
  if (overdue) params.set("overdue_days", "7");
  if (search) params.set("q", search);
  const nominations = useData<Page>(`/competitions/${id}/judges?${params}`);
  if (pilot.isPending) return <Loading />;
  if (!pilot.data) return <ErrorBox error={pilot.error} />;
  const details = pilot.data;
  const canManage = can(user, "judge_assign");
  const active = details.activation_status === "active";
  const documents = Object.fromEntries((details.documents || []).map((doc: Row) => [doc.kind, doc]));
  const visible = nominations.data?.items || [];
  const selectedJudges = visible.filter((nomination) => selected.includes(nomination.id));
  const mailReady = selectedJudges.filter((nomination) => active && nomination.judge.is_active_contact && nomination.judge.primary_email && !nomination.judge.do_not_contact && !nomination.archive_status && (!nomination.review_status || nomination.review_status === "approved"));
  function changeFilter(nextStatus: string, nextOverdue: boolean) {
    setStatus(nextStatus); setOverdue(nextOverdue); setPage(1); setSelected([]); setDeclineDraft(null);
  }
  async function saveResponse(nominationId: string, nextStatus: string) {
    setSelected(current => current.filter(selectedId => selectedId !== nominationId));
    try {
      await action.mutateAsync({ path: `/competitions/${id}/judges/${nominationId}`, method: "PATCH", body: { status: nextStatus } });
      setDeclineDraft(null);
    } catch {}
  }
  return (
    <>
      <Back to={tournamentPath(`/competitions/${details.category_slug}`, details.event_year)}>Competitions</Back>
      <Heading eyebrow={details.is_pilot ? `PILOT · ${details.event_year}` : `COMPETITION · ${details.event_year}`} title={details.name}>
        {active && <Link className="button primary" to={`/recruitment?event_year=${details.event_year}&competition_id=${id}&add=1`}><Plus size={16} /> Add judge</Link>}
        {details.is_pilot && can(user, "delete") && <button className="pilot-delete" onClick={() => setDeleteStep(1)}><Trash2 size={16} /> Delete pilot</button>}
      </Heading>
      <OfficialJudgeSlots
        competitionId={id} competitionName={details.name} active={active}
        assignments={details.official_assignments} coverage={details}
        canManage={canManage} candidate={candidate} onCloseCandidate={() => setCandidate(null)}
      />
      <div className="pilot-summary">
        <button onClick={() => changeFilter("", false)} className={!status && !overdue ? "active" : ""}><strong>{Object.values(details.counts || {}).reduce((sum: number, n: any) => sum + Number(n), 0)}</strong><span>All judges</span></button>
        {stages.slice(1).map(([key, title]) => <button key={key} onClick={() => changeFilter(status === key && !overdue ? "" : key, false)} className={status === key && !overdue ? "active" : ""}><strong>{details.counts?.[key] || 0}</strong><span>{title}</span></button>)}
      </div>
      <div className="two-col">
        {(["rules", "procedures"] as const).map((kind) => (
          <Panel key={kind} title={kind === "rules" ? "Rules" : "Procedures"}>
            {documents[kind] ? (
              <div className="pilot-document">
                <FileText size={20} />
                <a href={`/api/competitions/${id}/documents/${kind}`} target="_blank" rel="noreferrer">{documents[kind].filename}</a>
              </div>
            ) : <p>No {kind} uploaded yet.</p>}
            <button onClick={() => setDocumentKind(kind)}><Upload size={15} /> {documents[kind] ? "Replace" : "Upload"} {kind}</button>
          </Panel>
        ))}
      </div>
      <Panel title="Judges" action={<span className="caption">{nominations.data?.total ?? 0} in this view</span>}>
        <div className="pilot-filters">
          <Field label="Response status"><select value={status} onChange={(e) => changeFilter(e.target.value, false)}>{stages.map(([key, title]) => <option value={key} key={key}>{title}</option>)}</select></Field>
          <Field label="Search judges"><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); setSelected([]); setDeclineDraft(null); }} placeholder="Name or email" /></Field>
          <label className="pilot-overdue"><input type="checkbox" checked={overdue} onChange={(e) => changeFilter(e.target.checked ? "awaiting_response" : "", e.target.checked)} /> Waiting 7+ days</label>
        </div>
        <p className="caption">A completed sandbox message moves a judge from To contact to Awaiting response. No external email is sent.</p>
        {selected.length > 0 && <div className="selection-bar"><strong>{selected.length} selected</strong><button disabled={!mailReady.length} onClick={() => {
          if (mailReady.length !== selected.length) { setLocalError("Only selected judges with an email who have not declined or opted out can receive a sandbox draft."); return; }
          navigate(`/campaigns/new?competition_id=${id}`, { state: { judge_ids: mailReady.map((n) => n.judge.id) } });
        }}><Mail size={16} /> Email selected ({mailReady.length})</button></div>}
        <ErrorBox error={nominations.error || action.error || localError} />
        {nominations.isPending ? <Loading /> : visible.length ? (
          <div className="pilot-judge-list">
            <label className="pilot-select-all"><input type="checkbox" aria-label="Select visible judges" checked={visible.every((n) => selected.includes(n.id))} onChange={(e) => setSelected(e.target.checked ? visible.map((n) => n.id) : [])} /> Select visible judges</label>
            {visible.map((nomination) => (
              <article className="pilot-judge" key={nomination.id}>
                <input type="checkbox" aria-label={`Select ${nomination.judge.display_name}`} checked={selected.includes(nomination.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, nomination.id] : selected.filter((x) => x !== nomination.id))} />
                <div className="pilot-judge-main">
                  <Link to={`/campaigns/conversations/judges/${nomination.judge.id}`}><strong>{nomination.judge.display_name}</strong></Link> <UnreadMarker count={nomination.unread_count} />
                  <small>{nomination.judge.primary_email || "No email"}{nomination.judge.phone ? ` · ${nomination.judge.phone}` : ""}</small>
                  {(nomination.qualification_text || !!nomination.judge.qualifications?.length) && <p className="caption">{nomination.qualification_text || nomination.judge.qualifications.join("; ")}</p>}
                  {nomination.archive_status && <small>{label(nomination.archive_status)}</small>}
                  {!nomination.archive_status && nomination.status === "awaiting_response" && nomination.last_contacted_at && <small>Awaiting since {date(nomination.last_contacted_at)}</small>}
                  {nomination.official_assignment && <small className="official-assignment-summary">
                    {nomination.official_assignment.competition_id === id
                      ? `Official slot ${nomination.official_assignment.slot_number}`
                      : `Official slot in ${nomination.official_assignment.competition_name}`}
                  </small>}
                </div>
                <div className="nomination-controls">
                  <Field label="Response">
                    <select
                      value={declineDraft === nomination.id ? "declined" : nomination.status}
                      disabled={action.isPending || !active || !nomination.judge.is_active_contact || !!nomination.archive_status || nomination.official_assignment?.competition_id === id || ["pending", "rejected"].includes(nomination.review_status)}
                      onChange={(e) => {
                        const nextStatus = e.target.value;
                        setDeclineDraft(nextStatus === "declined" ? nomination.id : null);
                        if (nextStatus !== "declined" && nextStatus !== nomination.status) void saveResponse(nomination.id, nextStatus);
                      }}
                    >
                      {stages.slice(1).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
                    </select>
                  </Field>
                  {declineDraft === nomination.id && nomination.status !== "declined" && <div className="actions">
                    <button type="button" disabled={action.isPending} onClick={() => setDeclineDraft(null)}>Cancel</button>
                    <button type="button" disabled={action.isPending} onClick={() => saveResponse(nomination.id, "declined")}>Save decline</button>
                  </div>}
                  {nomination.review_status && <Link to={`/recruitment?event_year=${details.event_year}&candidate=${nomination.id}`}>View recruitment request</Link>}
                  {declineDraft !== nomination.id && canManage && active && !nomination.archive_status && details.confirmed_count < details.required_count && nomination.status === "accepted" && nomination.judge.is_active_contact && (!nomination.review_status || nomination.review_status === "approved") && (
                    <button type="button" onClick={() => setCandidate({ nominationId: nomination.id, judgeName: nomination.judge.display_name, current: nomination.official_assignment })}>
                      {nomination.official_assignment ? "Reassign judge" : "Confirm assignment"}
                    </button>
                  )}
                </div>
              </article>
            ))}
            <Pagination data={nominations.data} onPage={(next) => { setPage(next); setSelected([]); setDeclineDraft(null); }} />
          </div>
        ) : <Empty>No judges match this view.</Empty>}
      </Panel>
      {documentKind && <Modal title={`Upload ${documentKind}`} onClose={() => setDocumentKind("")}>
        <form onSubmit={async (event) => {
          event.preventDefault();
          const body = new FormData(event.currentTarget);
          try { await action.mutateAsync({ path: `/competitions/${id}/documents/${documentKind}`, method: "PUT", body }); setDocumentKind(""); } catch {}
        }}>
          <Field label="PDF or DOCX (up to 5 MB)"><input type="file" name="file" accept=".pdf,.docx" required /></Field>
          <ErrorBox error={action.error} />
          <button className="primary" disabled={action.isPending}>Save document</button>
        </form>
      </Modal>}
      {deleteStep > 0 && <Modal title="Delete pilot competition" onClose={() => { setDeleteStep(0); setDeleteName(""); }}>
        {deleteStep === 1 && <><p>Are you sure you want to delete <strong>{details.name}</strong>?</p><div className="form-footer"><button onClick={() => setDeleteStep(0)}>Cancel</button><button className="pilot-delete" onClick={() => setDeleteStep(2)}>Yes, continue</button></div></>}
        {deleteStep === 2 && <><p>Are you really sure? This pilot will disappear from competition lists. Its deletion remains in the audit trail.</p><div className="form-footer"><button onClick={() => setDeleteStep(0)}>Cancel</button><button className="pilot-delete" onClick={() => setDeleteStep(3)}>Continue to final confirmation</button></div></>}
        {deleteStep === 3 && <form onSubmit={async (event) => { event.preventDefault(); try { await action.mutateAsync({ path: `/competitions/${id}`, method: "DELETE", body: { confirmed_first: true, confirmed_second: true, exact_name: deleteName } }); navigate(tournamentPath(`/competitions/${details.category_slug}`, details.event_year)); } catch {} }}>
          <p>Type <strong>{details.name}</strong> exactly to delete this pilot.</p>
          <Field label="Competition name"><input value={deleteName} onChange={(event) => setDeleteName(event.target.value)} autoComplete="off" required /></Field>
          <ErrorBox error={action.error} />
          <div className="form-footer"><button type="button" onClick={() => setDeleteStep(0)}>Cancel</button><button className="pilot-delete" disabled={deleteName !== details.name || action.isPending}>Delete pilot</button></div>
        </form>}
      </Modal>}
    </>
  );
}
