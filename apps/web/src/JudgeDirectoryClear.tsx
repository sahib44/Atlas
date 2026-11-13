import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useAction, type Row } from "./api";
import { ErrorBox, Field, Modal } from "./ui";

export async function downloadSheet(path: string, filename: string) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw new Error("The judge sheet could not be exported.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function JudgeDirectoryClear() {
  const action = useAction();
  const [error, setError] = useState<unknown>();
  const [clearStep, setClearStep] = useState(0);
  const [password, setPassword] = useState("");
  const [backup, setBackup] = useState<Row | null>(null);
  const [cleared, setCleared] = useState<Row | null>(null);
  const [clearing, setClearing] = useState(false);
  return <>
    <button type="button" className="pilot-delete" onClick={() => { setClearStep(1); setBackup(null); setPassword(""); setError(undefined); }}><Trash2 size={16} /> Clear all judges</button>
    {cleared && <div className="notice" role="status">All {cleared.cleared} judge contacts were cleared. <a href={cleared.backup_url}>Download the saved backup</a></div>}
    {clearStep > 0 && <Modal title="Clear all judges" onClose={() => { if (!clearing) { setClearStep(0); setPassword(""); } }}>
      {clearStep === 1 && <><p>Are you sure you want to clear all judge contacts, nominations, and active assignments across every tournament?</p><div className="form-footer"><button onClick={() => setClearStep(0)}>Cancel</button><button className="pilot-delete" onClick={() => setClearStep(2)}>Yes, continue</button></div></>}
      {clearStep === 2 && <><p>Are you really sure? A sheet backup will be saved and downloaded before deletion. Competition definitions and email history will remain.</p><div className="form-footer"><button disabled={clearing} onClick={() => setClearStep(0)}>Cancel</button><button className="pilot-delete" disabled={clearing} onClick={async () => { setClearing(true); try { const saved = await action.mutateAsync({ path: "/judge-directory/clear/prepare" }); await downloadSheet(saved.download_url, "mist-deck-judges-backup.csv"); setBackup(saved); setClearStep(3); } catch (cause) { setError(cause); } finally { setClearing(false); } }}>{clearing ? "Saving backup…" : "Save backup and continue"}</button></div></>}
      {clearStep === 3 && <form onSubmit={async event => { event.preventDefault(); if (!backup) return; setClearing(true); try { const result = await action.mutateAsync({ path: "/judge-directory/clear", body: { confirmed_first: true, confirmed_second: true, password, backup_id: backup.id } }); setCleared(result); setClearStep(0); setPassword(""); } catch (cause) { setError(cause); } finally { setClearing(false); } }}><p>The sheet backup contains {backup?.judge_count} judges. Enter your signed-in account password to clear them.</p><Field label="Chair or director password"><input type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} /></Field><div className="form-footer"><button disabled={clearing} type="button" onClick={() => { setClearStep(0); setPassword(""); }}>Cancel</button><button className="pilot-delete" disabled={clearing || !password}>Clear all judges</button></div></form>}
      <ErrorBox error={error || action.error} />
    </Modal>}
  </>;
}
