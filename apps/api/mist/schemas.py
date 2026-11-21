from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class UserDepartmentView(BaseModel):
    slug: str
    name: str
    category_slug: str


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    full_name: str
    email: str
    role: str
    permissions: list[str]
    is_active: bool
    managed_account: bool = False
    department: UserDepartmentView | None = None
    recovery_required: bool = False


class ProfileUpdate(Input):
    full_name: str = Field(min_length=1, max_length=200)


class ObjectView(BaseModel):
    """Explicitly serialized public record; never accepts an ORM instance."""

    model_config = ConfigDict(extra="allow")
    id: UUID


class Page(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


class CatalogIn(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    is_active: bool = True


JudgeStatus = Literal[
    "prospective", "contacted", "interested", "confirmed", "declined", "inactive",
    "to_contact", "awaiting_response", "accepted"
]


class JudgeIn(Input):
    first_name: str = Field(default="", max_length=200)
    last_name: str = Field(default="", max_length=200)
    display_name: str = Field(min_length=1, max_length=400)
    primary_email: str = Field(default="", max_length=254)
    alternate_email: str = Field(default="", max_length=254)
    organization: str = Field(default="", max_length=300)
    job_title: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=50)
    status: JudgeStatus = "prospective"
    do_not_contact: bool = False
    notes: str = Field(default="", max_length=20000)
    qualifications: list[str] = Field(default_factory=list, max_length=100)
    categories: list[str] = Field(default_factory=list, max_length=100)
    years: list[int] = Field(default_factory=list, max_length=100)


class Bulk(Input):
    ids: list[UUID] = Field(min_length=1, max_length=1000)
    status: JudgeStatus | None = None
    category: str | None = None
    qualification: str | None = None
    confirmed: bool


class Mapping(Input):
    mapping: dict[str, str]


class Confirm(Input):
    confirmed: bool


class Resolve(Input):
    decision: Literal["merge", "create", "skip"]
    field_decisions: dict[str, Literal["existing", "incoming"]] = Field(
        default_factory=dict
    )
    confirmed: bool


class DirectRecipient(Input):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(default="", max_length=400)
    add_to_directory: bool = False


class CampaignIn(Input):
    request_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    subject_template: str = Field(min_length=1, max_length=500)
    body_template: str = Field(min_length=1, max_length=30000)
    judge_ids: list[UUID] = Field(default_factory=list, max_length=5000)
    recipients: list[DirectRecipient] = Field(default_factory=list, max_length=5000)
    pilot_competition_id: UUID | None = None
    event_year: int | None = Field(default=None, ge=2000, le=2200)
    allow_declined_outreach: bool = False
    variables: dict[str, str] = Field(default_factory=dict)


class ForwardIn(Input):
    recipient_email: str = Field(min_length=3, max_length=254)
    note: str = Field(default="", max_length=5000)


class PilotJudgeIn(Input):
    name: str = Field(min_length=1, max_length=400)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=50)
    qualifications: str = Field(default="", max_length=2000)
    existing_judge_id: UUID | None = None


class RecruitmentIn(Input):
    competition_id: UUID
    name: str = Field(min_length=1, max_length=400)
    email: str = Field(min_length=3, max_length=254)
    phone: str = Field(default="", max_length=50)
    qualifications: str = Field(min_length=1, max_length=2000)
    notes: str = Field(default="", max_length=5000)
    prior_involvement: str = Field(default="", max_length=300)
    existing_judge_id: UUID | None = None


class RecruitmentReview(Input):
    decision: Literal["approved", "rejected"]
    version: datetime


class JudgeDetailsIn(Input):
    qualifications: str = Field(max_length=2000)
    notes: str = Field(max_length=5000)


class PilotStatusIn(Input):
    status: Literal["to_contact", "awaiting_response", "accepted", "declined"]


class PilotDeleteIn(Input):
    confirmed_first: bool
    confirmed_second: bool
    exact_name: str = Field(min_length=1, max_length=200)


class OfficialAssignmentIn(Input):
    nomination_id: UUID
    slot_number: int = Field(ge=1, le=3)
    previous_assignment_id: UUID | None = None


class TicketIn(Input):
    request_id: UUID | None = None
    requester_name: str = Field(min_length=1, max_length=200)
    requester_email: str = Field(min_length=1, max_length=254)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=30000)


class Content(Input):
    body: str = Field(min_length=1, max_length=30000)
    request_id: UUID
    approval_id: UUID | None = None
    reply_subject: str | None = Field(default=None, max_length=1000)


class TicketStatus(Input):
    status: Literal[
        "new",
        "in_progress",
        "waiting_on_organizer",
        "waiting_on_requester",
        "resolved",
        "closed",
    ]
    reply_body: str = Field(default="", max_length=30000)
    reply_subject: str | None = Field(default=None, max_length=1000)
    internal_note: str = Field(default="", max_length=30000)
    resolution_note: str = Field(default="", max_length=30000)
    request_id: UUID = Field(default_factory=uuid4)
    approval_id: UUID | None = None
    expected_version: int | None = None


class UserUpdate(Input):
    role: Literal[
        "director",
        "technology_administrator",
        "competitions_administrator",
        "competitions_member",
        "competitions_chair",
    ]
    permissions: list[Literal["campaign_approver", "private_tickets"]]
    is_active: bool


class Failure(Input):
    enabled: bool


class MergeContacts(Input):
    merged_judge_id: UUID
    field_decisions: dict[str, Literal["existing", "incoming"]]
    confirmed: bool


class ClearJudges(Input):
    confirmed_first: bool
    confirmed_second: bool
    password: str = Field(min_length=1, max_length=200)
    backup_id: UUID


class IncomingEmail(Input):
    sender_name: str = Field(default="", max_length=400)
    sender_email: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=30000)
    request_id: UUID
    campaign_recipient_id: UUID | None = None


class ConversationRead(Input):
    read_through: datetime


class TicketDraftIn(Input):
    kind: Literal["messages", "notes"]
    body: str = Field(default="", max_length=30000)
    reply_subject: str | None = Field(default=None, max_length=1000)
    target_status: str = Field(default="", max_length=40)
    resolution_note: str = Field(default="", max_length=30000)
    expected_version: int
    request_id: UUID


class TicketDraftApprovalIn(Input):
    allow_public_reply: bool = False
