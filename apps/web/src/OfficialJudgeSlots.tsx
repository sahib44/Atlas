import { useState } from "react";
import { Link } from "react-router-dom";
import { UserCheck, UserPlus } from "lucide-react";
import { useAction, useData, date, label, Page } from "./api";
import { JudgeCoverage, Coverage } from "./CompetitionCoverage";
import { Empty, ErrorBox, Field, Loading, Modal, Pagination, Panel } from "./ui";

export type OfficialSlot = {
  id: string;
  slot_number: number;
  judge_id: string;
  judge_name: string;
};
type CurrentAssignment = {
  id: string;
  competition_id: string;
  competition_name: string;
  slot_number: number;
};
export type AssignmentCandidate = {
  nominationId: string;
  judgeName: string;
  current: CurrentAssignment | null;
};

export function OfficialJudgeSlots({
  competitionId, competitionName, active, assignments, coverage, canManage, candidate, onCloseCandidate,
}: {
  competitionId: string;
  competitionName: string;
  active: boolean;
  assignments: OfficialSlot[];
  coverage: Coverage;
  canManage: boolean;
  candidate: AssignmentCandidate | null;
  onCloseCandidate: () => void;
}) {
  const [releasing, setReleasing] = useState<OfficialSlot | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyPage, setHistoryPage] = useState(1);
  const path = `/competitions/${competitionId}/assignments`;
  const history = useData<Page>(`${path}/history?page=${historyPage}`, historyOpen);
  const action = useAction();
  const slots = [1, 2, 3];
  const vacant = slots.filter(slot => !assignments.some(assignment => assignment.slot_number === slot));

  return <Panel title="Official judge slots" className="official-roster" action={
    <button type="button" onClick={() => { setHistoryPage(1); setHistoryOpen(true); }}>Assignment history</button>
  }>
    {active
      ? <JudgeCoverage name={competitionName} {...coverage} />
      : <p className="caption">This competition is outside active tournament coverage.</p>}
    <ol className="official-slot-grid">
      {slots.map(slot => {
        const assignment = assignments.find(row => row.slot_number === slot);
        return <li className="official-slot" key={slot}>
          <span className="official-slot-label">Slot {slot}</span>
          {assignment ? <>
            <Link to={`/campaigns/conversations/judges/${assignment.judge_id}`} className="official-slot-judge">
              <UserCheck size={18} aria-hidden="true" /> {assignment.judge_name}
            </Link>
            {canManage && <button type="button" aria-label={`Release slot ${slot}`} onClick={() => { action.reset(); setReleasing(assignment); }}>Release</button>}
          </> : <span className="official-slot-empty"><UserPlus size={18} aria-hidden="true" /> Vacant</span>}
        </li>;
      })}
    </ol>
    {canManage && active && vacant.length > 0 && <p className="caption">Confirm judges with an Accepted response from the recruitment list below.</p>}

    {candidate && <Modal title={candidate.current ? "Reassign judge" : "Confirm official judge"} onClose={() => { action.reset(); onCloseCandidate(); }}>
      <form onSubmit={async event => {
        event.preventDefault();
        const slot = new FormData(event.currentTarget).get("slot");
        try {
          await action.mutateAsync({
            path, body: {
              nomination_id: candidate.nominationId,
              slot_number: Number(slot),
              previous_assignment_id: candidate.current?.id,
            },
          });
          onCloseCandidate();
        } catch {}
      }}>
        <p>Assign <strong>{candidate.judgeName}</strong> to <strong>{competitionName}</strong>.</p>
        {candidate.current && <p>This releases slot {candidate.current.slot_number} in {candidate.current.competition_name} and reserves the new slot in one action.</p>}
        {vacant.length ? <Field label="Official slot"><select name="slot" required>
          {vacant.map(slot => <option key={slot} value={slot}>Slot {slot}</option>)}
        </select></Field> : <p role="status">All three official slots are occupied.</p>}
        <ErrorBox error={action.error} />
        <div className="form-footer">
          <button type="button" onClick={() => { action.reset(); onCloseCandidate(); }}>Cancel</button>
          <button className="primary" disabled={action.isPending || !vacant.length}>{candidate.current ? "Reassign judge" : "Confirm assignment"}</button>
        </div>
      </form>
    </Modal>}

    {releasing && <Modal title="Release official slot" onClose={() => setReleasing(null)}>
      <p>Release slot {releasing.slot_number} held by <strong>{releasing.judge_name}</strong>? Their recruitment record will remain available.</p>
      <ErrorBox error={action.error} />
      <div className="form-footer">
        <button type="button" onClick={() => setReleasing(null)}>Cancel</button>
        <button className="pilot-delete" disabled={action.isPending} onClick={async () => {
          try {
            await action.mutateAsync({ path: `${path}/${releasing.id}`, method: "DELETE" });
            setReleasing(null);
          } catch {}
        }}>Release assignment</button>
      </div>
    </Modal>}

    {historyOpen && <Modal title="Assignment history" onClose={() => setHistoryOpen(false)}>
      <ErrorBox error={history.error} />
      {history.isPending ? <Loading /> : history.data?.items.length ? <>
        <div className="table-wrap"><table>
          <thead><tr><th>Slot</th><th>Judge</th><th>Confirmed</th><th>Released</th></tr></thead>
          <tbody>{history.data.items.map(row => <tr key={row.id}>
            <td>{row.slot_number}</td><td>{row.judge_name}</td><td>{date(row.created_at)}</td>
            <td>{row.released_at ? <>{date(row.released_at)}<small>{label(row.release_reason)}</small></> : "Current"}</td>
          </tr>)}</tbody>
        </table></div>
        <Pagination data={history.data} onPage={setHistoryPage} />
      </> : history.data && <Empty>No official assignments recorded yet.</Empty>}
    </Modal>}
  </Panel>;
}
