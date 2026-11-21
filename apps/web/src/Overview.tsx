import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertCircle, ArrowRight, ArrowUpRight, BookOpen, Box, Check, Clock, FileWarning,
  FolderKanban, Image, LifeBuoy, Mail, Palette, PenLine, Plus, ShieldCheck,
  Trophy, UserRoundPlus, Users, Utensils,
} from "lucide-react";
import { useData, Page, Row, can, label, date, type Department } from "./api";
import { useUser } from "./App";
import { UnreadMarker } from "./Inbox";
import { Coverage, JudgeCoverage, tournamentPath, useTournament } from "./CompetitionCoverage";
import {
  Heading, Panel, Loading, ErrorBox, Empty, Badge, Pagination, JsonDetails, Modal,
} from "./ui";

type Metric = { label: string; value: number; link: string };
type DashboardData = {
  metrics: Metric[];
  groups: Record<string, Metric[]>;
  recent_activity: Array<{ id: string; summary: string; action: string; created_at: string }>;
  latest_import: {
    id: string;
    filename: string;
    status: string;
    created_count: number;
    updated_count: number;
    skipped_count: number;
  } | null;
  overdue_definition: string;
};
type CompetitionPreview = Coverage & {
  id: string;
  name: string;
  category_slug: string;
  event_year: number;
  is_pilot: boolean;
  unread_count: number;
};
type CampaignPreview = { id: string; name: string; status: string };
type DeliverySummary = { recipient_count: number; counts: Record<string, number> };
type PreviewPage<T> = { items: T[]; total: number };
type DepartmentDashboard = {
  items: CompetitionPreview[];
  activity: Array<{ id: string; summary: string; created_at: string }>;
};

const actionLinks = [
  { link: "/campaigns?status=pending_approval", label: "Campaigns awaiting approval", icon: ShieldCheck },
  { link: "/duplicates", label: "Duplicate reviews", icon: FileWarning },
  { link: "/tickets?view=overdue", label: "Overdue tickets", icon: Clock },
  { link: "/outbox?status=failed", label: "Failed sandbox messages", icon: AlertCircle },
];

const competitionIcons: Record<string, typeof FolderKanban> = {
  "2D Art": Image,
  "3D Art": Box,
  "Culinary Arts": Utensils,
  "Extemporaneous Drawing": PenLine,
};
const categoryIcons: Record<string, typeof FolderKanban> = {
  arts: Palette,
  "knowledge-quran": BookOpen,
  "writing-oratory": PenLine,
  brackets: Trophy,
  "group-projects": Users,
  "sports-referees": Trophy,
};

function ViewAll({ to, children = "View all" }: { to: string; children?: string }) {
  return <Link className="overview-view-all" to={to}>{children}<ArrowRight size={15} aria-hidden="true" /></Link>;
}

function CampaignProgressItem({ campaign }: { campaign: CampaignPreview }) {
  const tracksDelivery = ["queued", "sending", "completed", "partially_failed", "cancelled"].includes(campaign.status);
  const delivery = useData<DeliverySummary>(`/campaigns/${campaign.id}`, tracksDelivery);
  const total = delivery.data?.recipient_count ?? 0;
  const sent = delivery.data?.counts.sent ?? 0;
  const failed = delivery.data?.counts.failed ?? 0;
  return <div className="campaign-progress-item">
    <div className="campaign-progress-heading">
      <Link to={`/campaigns/${campaign.id}`}>{campaign.name}</Link>
      {total > 0 ? <strong>{Math.round(100 * sent / total)}%</strong> : <Badge value={campaign.status} />}
    </div>
    {total > 0 && <>
      <progress className="campaign-progress-meter" value={sent} max={total} aria-label={`${campaign.name} sandbox delivery`} />
      <small>{sent}/{total} sandbox messages sent{failed > 0 && ` · ${failed} failed`}</small>
    </>}
    {tracksDelivery && delivery.isPending && <small>Loading delivery progress…</small>}
    {delivery.error && <small>Delivery progress unavailable</small>}
  </div>;
}

function CompetitionCards({ items }: { items: CompetitionPreview[] }) {
  return <div className="overview-competition-grid">
    {items.map(competition => {
      const Icon = competitionIcons[competition.name] || categoryIcons[competition.category_slug] || FolderKanban;
      return <Link className="overview-competition" key={competition.id} to={`/competitions/${competition.category_slug}/${competition.id}`}>
        <Icon size={25} aria-hidden="true" />
        <strong>{competition.name} <UnreadMarker count={competition.unread_count} /></strong>
        <JudgeCoverage name={competition.name} confirmed_count={competition.confirmed_count} required_count={competition.required_count} />
        <span className="overview-competition-year">{competition.is_pilot && "Pilot · "}{competition.event_year}</span>
      </Link>;
    })}
  </div>;
}

function DepartmentOverview({ department, tournament }: { department: Department; tournament: ReturnType<typeof useTournament> }) {
  const query = useData<DepartmentDashboard>(tournamentPath("/accounts/department-dashboard", tournament.year),
    !tournament.isPending && !tournament.error);
  const items = query.data?.items;
  const openSlots = items?.reduce((total, item) => total + Math.max(0, item.required_count - item.confirmed_count), 0);
  return <Panel className="department-overview" title={department.name}
    action={<div className="actions">
      <ViewAll to={tournamentPath(`/competitions/${department.category_slug}`, tournament.year)}>
        {`Open ${department.name} competitions`}
      </ViewAll>
      <ViewAll to={tournamentPath("/competitions", tournament.year)}>Open all competitions</ViewAll>
    </div>}>
    <p className="department-summary">
      Your primary competitions{openSlots !== undefined && <> · {openSlots} judge {openSlots === 1 ? "slot" : "slots"} open</>}
    </p>
    <ErrorBox error={tournament.error || query.error} />
    {!tournament.error && (query.isPending ? <Loading /> : items && (items.length
      ? <CompetitionCards items={items} />
      : <Empty>No assigned competitions for the selected tournament.</Empty>))}
    {query.data && <>
      <h3>Competitions activity</h3>
      {query.data.activity.length ? <ul className="department-activity">
        {query.data.activity.map(event => <li key={event.id} className="overview-activity-row">
          <Clock size={17} aria-hidden="true" />
          <span>{event.summary}</span>
          <time dateTime={event.created_at}>{date(event.created_at)}</time>
        </li>)}
      </ul> : <Empty>No recent competition activity.</Empty>}
    </>}
  </Panel>;
}

export function Dashboard() {
  const query = useData<DashboardData>("/dashboard");
  const user = useUser();
  const showCompetitions = can(user, "judges");
  const showCampaigns = can(user, "campaigns");
  const showDirectory = showCompetitions || can(user, "imports");
  const canReviewJudges = can(user, "recruitment_review");
  const tournament = useTournament(showDirectory);
  const directorySummary = useData<{ confirmed: number }>(`/judge-directory/summary?event_year=${tournament.year}`, showDirectory && tournament.year !== null);
  const recruitmentSummary = useData<{ requests: number }>(`/recruitment/summary?event_year=${tournament.year}`, canReviewJudges && tournament.year !== null);
  const judgeRequestsHref = `/recruitment?view=requests${tournament.year === null ? "" : `&event_year=${tournament.year}`}`;
  const competitionParams = new URLSearchParams({ page_size: "4" });
  if (tournament.year !== null) competitionParams.set("event_year", String(tournament.year));
  const competitions = useData<PreviewPage<CompetitionPreview>>(`/competitions?${competitionParams}`, showCompetitions && !tournament.isPending && !tournament.error);
  const competitionsHref = tournamentPath("/competitions", tournament.year);
  const campaigns = useData<PreviewPage<CampaignPreview>>("/campaigns?page_size=3", showCampaigns);
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorBox error={query.error} />;

  const data = query.data;
  const metrics = new Map(data.metrics.map(metric => [metric.link, metric]));
  const summaryCards = [
    { link: "/judges", label: "Confirmed judges", icon: Users, value: directorySummary.data?.confirmed },
    { link: "/outbox?status=sent", label: "Sandbox messages sent", icon: Mail, value: metrics.get("/outbox?status=sent")?.value },
    { link: "/tickets?view=open", label: "Open tickets", icon: LifeBuoy, value: metrics.get("/tickets?view=open")?.value },
    { link: competitionsHref, label: tournament.year ? `Competitions · ${tournament.year}` : "Competitions", icon: FolderKanban, value: showCompetitions ? competitions.data?.total : undefined },
  ].filter(card => card.value !== undefined);
  const availableActions = actionLinks.filter(item => metrics.has(item.link));
  const flaggedActions = availableActions.filter(item => (metrics.get(item.link)?.value ?? 0) > 0);
  const draftCount = metrics.get("/campaigns?status=draft")?.value;
  const queuedCount = metrics.get("/outbox?status=queued")?.value;
  const ticketStatuses = data.groups["Tickets by status"];

  return <div className="overview">
    <Heading title="Operations overview">
      {can(user, "tickets") && <Link className="button" to="/tickets/new"><Plus size={16} /> New simulated ticket</Link>}
      {showCampaigns && <Link className="button primary" to="/campaigns/new"><Plus size={16} /> Create campaign</Link>}
    </Heading>
    <ErrorBox error={query.error} />

    {user?.department && <DepartmentOverview department={user.department} tournament={tournament} />}

    {summaryCards.length > 0 && <section className="overview-metrics" aria-label="Workspace metrics">
      {summaryCards.map(({ link, label: title, icon: Icon, value }) => (
        <Link className="overview-metric" to={link} key={link}>
          <span className="overview-icon"><Icon size={23} aria-hidden="true" /></span>
          <span className="overview-metric-copy"><strong>{value?.toLocaleString()}</strong><span>{title}</span></span>
          <ArrowUpRight className="metric-arrow" size={15} aria-hidden="true" />
        </Link>
      ))}
    </section>}

    {(canReviewJudges || availableActions.length > 0) && <Panel className="overview-action-panel" title="Action required">
      <ErrorBox error={recruitmentSummary.error} />
      <div className="overview-action-list">
        {canReviewJudges && <Link className="overview-action" to={judgeRequestsHref}>
          <UserRoundPlus size={17} aria-hidden="true" />
          <span>Judge requests awaiting approval</span><strong>{recruitmentSummary.data?.requests ?? "—"}</strong>
          <ArrowRight size={15} aria-hidden="true" />
        </Link>}
        {flaggedActions.map(({ link, label: title, icon: Icon }) => (
          <Link className="overview-action" to={link} key={link}>
            <Icon size={17} aria-hidden="true" />
            <span>{title}</span><strong>{metrics.get(link)?.value}</strong>
            <ArrowRight size={15} aria-hidden="true" />
          </Link>
        ))}
        {!canReviewJudges && !flaggedActions.length && <p className="overview-clear"><Check size={17} aria-hidden="true" /> No items flagged for review in this overview.</p>}
      </div>
    </Panel>}

    {(showCampaigns || showCompetitions || queuedCount !== undefined) && <div className="overview-work">
      {showCampaigns ? <Panel className="overview-campaigns" title="Campaign progress" action={<ViewAll to="/campaigns" />}>
        <ErrorBox error={campaigns.error} />
        {campaigns.isPending ? <Loading /> : campaigns.data?.items.length ? (
          <div className="campaign-progress-list">
            {campaigns.data.items.map(campaign => <CampaignProgressItem campaign={campaign} key={campaign.id} />)}
          </div>
        ) : campaigns.data && <Empty>No outreach campaigns yet.</Empty>}
        <div className="overview-panel-footer">
          {draftCount !== undefined && <Link to="/campaigns?status=draft">{draftCount} {draftCount === 1 ? "draft" : "drafts"}</Link>}
          {queuedCount !== undefined && <Link to="/outbox?status=queued">{queuedCount} sandbox messages queued</Link>}
        </div>
      </Panel> : queuedCount !== undefined && <Panel title="Sandbox outbox" action={<ViewAll to="/outbox" />}>
        <Link className="stat-row" to="/outbox?status=queued"><span>Sandbox messages queued</span><strong>{queuedCount}</strong><ArrowRight size={15} /></Link>
      </Panel>}
      {showCompetitions && <Panel className="overview-competitions" title="Competitions" action={<ViewAll to={competitionsHref} />}>
        <ErrorBox error={tournament.error || competitions.error} />
        {!tournament.error && (competitions.isPending ? <Loading /> : competitions.data?.items.length
          ? <CompetitionCards items={competitions.data.items} />
          : competitions.data && <Empty>No competitions found.</Empty>)}
      </Panel>}
    </div>}

    {can(user, "audit") && <Panel className="overview-activity" title="Recent activity" action={<ViewAll to="/audit" />}>
      {data.recent_activity.length ? data.recent_activity.slice(0, 5).map(event => (
        <Link to={`/audit?q=${encodeURIComponent(event.action)}`} key={event.id} className="overview-activity-row">
          <ShieldCheck size={17} aria-hidden="true" />
          <span>{event.summary}</span>
          <time dateTime={event.created_at}>{date(event.created_at)}</time>
        </Link>
      )) : <Empty>No recent activity.</Empty>}
    </Panel>}

    {(ticketStatuses || data.latest_import) && <div className="overview-secondary">
      {ticketStatuses && <Panel title="Tickets by status">
        {ticketStatuses.length ? ticketStatuses.map(item => (
          <Link className="stat-row" key={item.label} to={item.link}>
            <span>{label(item.label)}</span><strong>{item.value}</strong><ArrowRight size={15} aria-hidden="true" />
          </Link>
        )) : <Empty>No tickets yet.</Empty>}
        <p className="caption overview-overdue-definition">{data.overdue_definition}</p>
      </Panel>}
      {data.latest_import && <Panel title="Latest import" action={<ViewAll to={`/imports/${data.latest_import.id}`}>View import</ViewAll>}>
        <div className="summary-strip">
          <strong>{data.latest_import.filename}</strong>
          <Badge value={data.latest_import.status} />
          <span>{data.latest_import.created_count} created</span>
          <span>{data.latest_import.updated_count} updated</span>
          <span>{data.latest_import.skipped_count} skipped</span>
        </div>
      </Panel>}
    </div>}
  </div>;
}

export function AuditPage() {
  const [params, setParams] = useSearchParams();
  const query = useData<Page>("/audit-events?" + params);
  const [selected, setSelected] = useState<string | null>(null);
  const event = query.data?.items.find(row => row.id === selected);
  return (
    <>
      <Heading
        title="Audit trail"
      />
      <div className="toolbar">
        <input
          aria-label="Search audit events"
          placeholder="Search actions or summaries…"
          value={params.get("q") || ""}
          onChange={(e) => setParams({ q: e.target.value })}
        />
      </div>
      <ErrorBox error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Action</th>
                <th>Summary</th>
                <th>Recorded</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Badge value={r.action} />
                  </td>
                  <td>{r.summary}</td>
                  <td>{date(r.created_at)}</td>
                  <td>
                    <button onClick={() => setSelected(r.id)}>Inspect</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {query.data?.total === 0 && <Empty />}
          <Pagination
            data={query.data}
            onPage={(n) => {
              params.set("page", String(n));
              setParams(params);
            }}
          />
        </div>
      )}
      {event && (
        <Modal title="Audit event" onClose={() => setSelected(null)}>
          <JsonDetails data={event} />
        </Modal>
      )}
    </>
  );
}
