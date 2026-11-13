import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Calendar, Check, ClipboardList, Download, Ellipsis, GraduationCap, Info, Layers3, Mail, Pencil, RefreshCcw, SquareCheck, Trophy, Users } from "lucide-react";
import { useAction, useData, can, label, type Page, type Row } from "./api";
import { useUser } from "./App";
import { TournamentSelector } from "./CompetitionCoverage";
import { Empty, ErrorBox, Loading, Modal, Pagination } from "./ui";
import { ContactLines, JudgeConversations, JudgeDetailsEditor, JudgeIdentity, JudgeMetric, JudgeSidePanel, SortColumn, WorkspaceFilters, WorkspacePagination, WorkspaceTabs, judgeDate, useJudgeWorkspace } from "./JudgeWorkspace";
import { downloadSheet, JudgeDirectoryClear } from "./JudgeDirectoryClear";

type Summary = { confirmed: number; competitions: number; required: number };

export function JudgeDirectory() {
  const workspace = useJudgeWorkspace();
  const { params, change, tournament, year } = workspace;
  const user = useUser();
  const navigate = useNavigate();
  const queryString = workspace.scope(["q", "category_slug", "competition_id", "sort", "direction"]);
  const query = useData<Page>(`/judge-directory?${queryString}`, year !== null);
  const summary = useData<Summary>(`/judge-directory/summary?event_year=${year}`, year !== null);
  const profileId = params.get("profile");
  const profile = useData<Row>(`/judge-directory/assignments/${profileId}`, !!profileId && year !== null);
  const [selection, setSelection] = useState<{ scope: string; ids: string[] }>({ scope: "", ids: [] });
  const [error, setError] = useState<unknown>();
  const selected = selection.scope === String(queryString) ? selection.ids : [];
  const rows = query.data?.items || [];
  const visible = rows.map(row => row.judge_id as string);
  const select = (ids: string[]) => setSelection({ scope: String(queryString), ids });
  const open = (id: string) => change({ profile: id }, false);
  const data = profile.data?.event_year === year ? profile.data : undefined;
  const sort = (key: string, title: string) => <SortColumn label={title} active={(params.get("sort") || "name") === key}
    direction={params.get("direction") || "asc"} onClick={() => workspace.sort(key, "name", "asc")} />;
  async function exportCsv() {
    setError(undefined);
    try { await downloadSheet(`/api/judge-directory/export?${queryString}`, `mist-deck-confirmed-${year}.csv`); }
    catch (cause) { setError(cause); }
  }

  return <div className={`judge-workspace directory-workspace ${profileId ? "with-detail" : ""}`}>
    <section className="judge-results" aria-label="Judge directory">
      <div className="judge-heading">
        <div><h1>Judge Directory</h1><p>Confirmed judges and assignments{year ? ` for the ${year} tournament` : ""}.</p></div>
        <div className="judge-heading-actions">
          <TournamentSelector tournament={tournament} />
          <button type="button" className="judge-export" disabled={year === null} onClick={exportCsv}><Download size={18} /> Export CSV</button>
        </div>
      </div>
      <div className="judge-metrics directory-metrics">
        <JudgeMetric icon={<Users />} value={summary.data?.confirmed ?? "—"} label="Confirmed Judges" />
        <JudgeMetric icon={<Trophy />} value={summary.data?.competitions ?? "—"} label="Active Competitions" />
        <JudgeMetric icon={<SquareCheck />} value={summary.data ? <>{summary.data.confirmed} / {summary.data.required}
          <Info size={15} aria-label="Confirmed assignments out of required slots" /></> : "—"} label="Assigned Slots" color="green" />
      </div>
      <WorkspaceFilters workspace={workspace} extra={can(user, "delete") ? <JudgeDirectoryClear /> : null} />
      {!!selected.length && can(user, "campaigns") && <div className="judge-bulk">
        <span>{selected.length} selected</span><button type="button" onClick={() => navigate(`/campaigns/new?event_year=${year}`, { state: { judge_ids: selected } })}><Mail size={15} /> Email selected</button>
        <button type="button" onClick={() => select([])}>Clear selection</button>
      </div>}
      <ErrorBox error={error || tournament.error || query.error || summary.error || workspace.options.error} />
      {year === null ? tournament.isPending ? <Loading /> : <Empty>No tournament is available.</Empty> :
        query.isPending ? <Loading /> : <div className="judge-table-wrap">
          <table className="judge-table"><thead><tr><th className="judge-check"><input type="checkbox" aria-label="Select visible judges"
            checked={!!visible.length && visible.every(id => selected.includes(id))} onChange={e => select(e.target.checked ? visible : [])} /></th>
            {sort("name", "Judge")}{sort("competition", "Competition")}{sort("slot", "Slot")}<th scope="col">Contact</th>{sort("assignment", "Assignment")}
            <th className="judge-more"><span className="visually-hidden">Actions</span></th></tr></thead>
            <tbody>{rows.map(row => <tr key={row.id} className={profileId === row.id ? "judge-selected" : ""}>
              <td><input type="checkbox" aria-label={`Select ${row.name}`} checked={selected.includes(row.judge_id)} onChange={e => {
                select(e.target.checked ? [...selected, row.judge_id] : selected.filter(id => id !== row.judge_id));
                if (e.target.checked) open(row.id);
              }} /></td>
              <td><button type="button" className="judge-row-name" aria-expanded={profileId === row.id}
                onClick={() => open(profileId === row.id ? "" : row.id)}>{row.name}</button></td>
              <td><Link className="judge-competition-chip" to={`/competitions/${row.category_slug}/${row.competition_id}`}>{row.competition} ({row.category})</Link></td>
              <td className="judge-date">Slot {row.slot_number}</td><td><ContactLines email={row.email} phone={row.phone} /></td>
              <td className="judge-date">Confirmed {judgeDate(row.created_at)}</td>
              <td><button type="button" className="judge-plain" aria-label={`Details for ${row.name}`} onClick={() => open(row.id)}><Ellipsis size={19} /></button></td>
            </tr>)}</tbody></table>
          {!rows.length && <Empty>No confirmed judges match these filters.</Empty>}
        </div>}
      <WorkspacePagination data={query.data} noun="judges" onPage={page => change({ page: String(page) }, false)} />
    </section>
    {profileId && (data ? <DirectoryPanel key={data.id} row={data} close={() => open("")}
      onWithdraw={() => { select(selected.filter(id => id !== data.judge_id)); open(""); }} /> :
      <JudgeSidePanel title="Judge profile" badge={null} close={() => open("")}><div className="judge-panel-body">
        {profile.isPending ? <Loading /> : <ErrorBox error={profile.error || "This assignment is in another tournament."} />}
      </div></JudgeSidePanel>)}
  </div>;
}

function DirectoryPanel({ row, close, onWithdraw }: { row: Row; close: () => void; onWithdraw: () => void }) {
  const [tab, setTab] = useState("profile");
  const [withdrawing, setWithdrawing] = useState(false);
  const [editingDetails, setEditingDetails] = useState(false);
  const [page, setPage] = useState(1);
  const [detailsPage, setDetailsPage] = useState(1);
  const user = useUser();
  const action = useAction();
  const history = useData<Page>(`/judge-directory/assignments/${row.id}/history?page=${page}`, tab === "history");
  const detailsHistory = useData<Page>(`/judge-directory/assignments/${row.id}/details-history?page=${detailsPage}`, tab === "history");
  const competitionPath = `/competitions/${row.category_slug}/${row.competition_id}`;
  return <JudgeSidePanel title={row.name} badge={<span className="judge-pill confirmed"><Check size={16} /> Confirmed Assignment</span>} close={close}>
    <WorkspaceTabs name="Judge profile views" value={tab} onChange={setTab} labels={[
      ["profile", "Profile"], ...(can(user, "campaigns") ? [["conversations", "Conversations"] as [string, string]] : []), ["history", "History"],
    ]} />
    {tab === "profile" && <div className="judge-panel-body">
      <JudgeIdentity row={row} />
      <dl className="directory-details">
        <div><Trophy size={18} /><dt>Competition</dt><dd><Link to={competitionPath}>{row.competition} ({row.category})</Link></dd></div>
        <div><Layers3 size={18} /><dt>Slot</dt><dd>Slot {row.slot_number} of 3</dd></div>
        <div><Calendar size={18} /><dt>Assigned</dt><dd>{judgeDate(row.created_at)}</dd></div>
        <div className="directory-text"><GraduationCap size={18} /><dt>Qualifications</dt><dd>{row.qualification || "No qualifications recorded."}</dd></div>
        <div className="directory-text"><ClipboardList size={18} /><dt>Notes</dt><dd>{row.notes || "No notes recorded."}</dd></div>
        {row.contact_notes && row.contact_notes !== row.notes && <div className="directory-text">
          <ClipboardList size={18} /><dt>Contact notes</dt><dd>{row.contact_notes}</dd>
        </div>}
      </dl>
      <div className="directory-profile-actions"><Link className="button" to={competitionPath}>View competition</Link>
        {can(user, "campaigns") && row.email && !row.do_not_contact && <Link className="button"
          to={`/campaigns/new?competition_id=${row.competition_id}&event_year=${row.event_year}`}
          state={{ judge_ids: [row.judge_id] }}><Mail size={14} /> Email judge</Link>}
        {(can(user, "judges") || can(user, "imports")) && <button type="button" onClick={() => setEditingDetails(true)}><Pencil size={14} /> Edit qualifications and notes</button>}
        {can(user, "judges") && <button type="button" className="directory-unconfirm" onClick={() => { action.reset(); setWithdrawing(true); }}><RefreshCcw size={14} /> Unconfirm</button>}
      </div>
    </div>}
    {tab === "conversations" && <JudgeConversations id={row.judge_id} />}
    {tab === "history" && <div className="judge-panel-body"><ErrorBox error={history.error || detailsHistory.error} />
      <h3>Profile edits</h3>
      {detailsHistory.isPending ? <Loading /> : detailsHistory.data?.items.length ? <>
        {detailsHistory.data.items.map(item => <article className="judge-detail-card" key={item.id}>
          <strong>{item.summary}</strong><small>{item.actor || "System"} · {judgeDate(item.created_at)}</small>
        </article>)}
        <Pagination data={detailsHistory.data} onPage={setDetailsPage} />
      </> : <Empty>No profile edits recorded.</Empty>}
      <h3>Assignments</h3>
      {history.isPending ? <Loading /> : history.data?.items.map(item => <article className="judge-detail-card" key={item.id}>
        <strong>{item.competition} · Slot {item.slot_number}</strong><small>Confirmed {judgeDate(item.created_at)}</small>
        {item.released_at && <small>{label(item.release_reason)} · {judgeDate(item.released_at)}</small>}
      </article>)}<Pagination data={history.data} onPage={setPage} />
    </div>}
    {editingDetails && <JudgeDetailsEditor path={`/judge-directory/assignments/${row.id}/details`}
      qualifications={row.qualification || ""} notes={row.notes || ""} close={() => setEditingDetails(false)} />}
    {withdrawing && <Modal title="Unconfirm judge" onClose={() => setWithdrawing(false)}>
      <p>Release {row.name}’s slot in {row.competition} and archive this assignment as Withdrawn?</p>
      <ErrorBox error={action.error} />
      <div className="form-footer"><button onClick={() => setWithdrawing(false)}>Cancel</button>
        <button className="pilot-delete" disabled={action.isPending} onClick={async () => {
          try { await action.mutateAsync({ path: `/recruitment/assignments/${row.id}/withdraw` }); setWithdrawing(false); onWithdraw(); } catch {}
        }}>Unconfirm assignment</button>
      </div>
    </Modal>}
  </JudgeSidePanel>;
}
