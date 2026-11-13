import { useSearchParams } from "react-router-dom";
import { useData } from "./api";

const tournamentPreference = "mist-tournament-year";
type TournamentOptions = { years: number[]; default_year: number | null };
export type Coverage = { confirmed_count: number; required_count: number };

export function useTournament(enabled = true) {
  const [params, setParams] = useSearchParams();
  const options = useData<TournamentOptions>("/competitions/tournaments", enabled);
  const years = options.data?.years ?? [];
  const requested = Number(params.get("event_year"));
  const preferred = Number(localStorage.getItem(tournamentPreference));
  const year = [requested, preferred].find(value => years.includes(value)) ?? options.data?.default_year ?? null;

  function selectYear(value: string) {
    localStorage.setItem(tournamentPreference, value);
    const next = new URLSearchParams(params);
    next.set("event_year", value);
    for (const key of ["page", "competition_id", "candidate", "profile", "add"]) next.delete(key);
    setParams(next);
  }

  return { year, years, selectYear, isPending: options.isPending, error: options.error };
}

export function tournamentPath(path: string, year: number | null) {
  return year === null ? path : `${path}?event_year=${year}`;
}

export function TournamentSelector({ tournament }: { tournament: ReturnType<typeof useTournament> }) {
  if (!tournament.years.length) return null;
  return <label className="tournament-selector">
    <span>Tournament</span>
    <select value={tournament.year ?? ""} onChange={event => tournament.selectYear(event.target.value)}>
      {tournament.years.map(year => <option key={year} value={year}>{year}</option>)}
    </select>
  </label>;
}

export function JudgeCoverage({ confirmed_count, required_count, name }: Coverage & { name: string }) {
  if (required_count === 0) return <span className="coverage-label">No active competitions</span>;
  return <div className="judge-coverage">
    <span className="coverage-label"><strong>{confirmed_count}/{required_count}</strong> confirmed</span>
    <progress className="campaign-progress-meter" value={confirmed_count} max={required_count} aria-label={`${name} confirmed judge coverage`} />
  </div>;
}
