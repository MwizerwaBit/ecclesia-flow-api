from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl, StringConstraints, field_validator, model_validator

from app.modules.activity import recurrence

EventType = Literal["service", "meeting", "event", "prayer", "outreach", "other"]
Visibility = Literal["public", "members", "private"]
EventStatus = Literal["draft", "pending_review", "changes_requested", "published", "canceled", "completed"]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class EventTheme(BaseModel):
    """How the event looks on its page and card — chosen by the organiser."""

    accent_color: HexColor | None = None
    layout: Literal["classic", "banner", "minimal"] = "classic"


def _cover(value: str | None) -> str | None:
    # Covers come from our own media store (relative /media-files/… URL) or an
    # https image URL — never javascript:, data: or plain http.
    if value is None or value == "":
        return None
    if value.startswith("/media-files/") or value.startswith("https://"):
        return value
    raise ValueError("Cover image must be an uploaded image or an https:// link")


class EventListItem(BaseModel):
    id: str
    title: str
    type: str
    start_date_time: datetime
    end_date_time: datetime | None = None
    location: str | None
    status: str
    attendee_count: int | None
    is_public: bool
    visibility: Visibility = "members"
    unit_id: str | None = None
    group_id: str | None = None
    cover_image_url: str | None = None
    theme: EventTheme = EventTheme()
    is_recurring: bool = False
    recurrence_rule: str | None = None
    next_occurrence: datetime | None = None
    pending_reviews: int = 0


class EventReviewRead(BaseModel):
    reviewer_user_id: str
    reviewer_name: str
    status: Literal["pending", "approved", "changes_requested"]
    comment: str | None
    decided_at: datetime | None


class ChurchEventRead(BaseModel):
    id: str
    tenant_id: str
    title: str
    type: str
    description: str | None
    location: str | None
    start_date_time: datetime
    end_date_time: datetime | None
    is_recurring: bool
    recurrence_rule: str | None
    status: str
    unit_id: str | None
    unit_name: str | None
    attendance_mode: str
    is_public: bool
    visibility: Visibility = "members"
    group_id: str | None = None
    group_name: str | None = None
    cover_image_url: str | None = None
    theme: EventTheme = EventTheme()
    online_url: str | None = None
    capacity: int | None = None
    submitted_at: datetime | None = None
    published_at: datetime | None = None
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    reviews: list[EventReviewRead] = []
    upcoming_occurrences: list[datetime] = []


class _EventFields(BaseModel):
    @field_validator("recurrence_rule", check_fields=False)
    @classmethod
    def _rule(cls, v: str | None) -> str | None:
        return recurrence.validate(v) if v else None

    @field_validator("cover_image_url", check_fields=False)
    @classmethod
    def _cover_url(cls, v: str | None) -> str | None:
        return _cover(v)


class ChurchEventCreate(_EventFields):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)]
    type: EventType
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    location: Annotated[str, StringConstraints(max_length=200)] | None = None
    start_date_time: datetime
    end_date_time: datetime | None = None
    recurrence_rule: str | None = None
    unit_id: str | None = None
    attendance_mode: Literal["individual", "headcount"] = "individual"
    visibility: Visibility = "members"
    group_id: str | None = None
    cover_image_url: str | None = None
    theme: EventTheme = Field(default_factory=EventTheme)
    online_url: HttpUrl | None = None
    capacity: Annotated[int, Field(gt=0, le=100000)] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "ChurchEventCreate":
        if self.end_date_time and self.end_date_time <= self.start_date_time:
            raise ValueError("The event must end after it starts")
        if self.visibility == "private" and not self.group_id:
            raise ValueError("A private event belongs to a group — choose which one")
        return self


class ChurchEventUpdate(_EventFields):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)] | None = None
    type: EventType | None = None
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    location: Annotated[str, StringConstraints(max_length=200)] | None = None
    start_date_time: datetime | None = None
    end_date_time: datetime | None = None
    recurrence_rule: str | None = None
    unit_id: str | None = None
    attendance_mode: Literal["individual", "headcount"] | None = None
    visibility: Visibility | None = None
    group_id: str | None = None
    cover_image_url: str | None = None
    theme: EventTheme | None = None
    online_url: HttpUrl | None = None
    capacity: Annotated[int, Field(gt=0, le=100000)] | None = None


class SubmitForReviewInput(BaseModel):
    reviewer_user_ids: list[str] = Field(min_length=1, max_length=5)


class ReviewDecisionInput(BaseModel):
    decision: Literal["approved", "changes_requested"]
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    @model_validator(mode="after")
    def _say_why(self) -> "ReviewDecisionInput":
        if self.decision == "changes_requested" and not self.comment:
            raise ValueError("Say what needs to change")
        return self


class ReviewerOption(BaseModel):
    user_id: str
    name: str
    role_name: str


class AttendanceMarkInput(BaseModel):
    event_id: str | None = None
    member_id: str


class AttendanceRecordRead(BaseModel):
    id: str
    tenant_id: str
    event_id: str
    mode: str
    member_id: str | None
    adult_count: int | None
    child_count: int | None
    total_count: int | None
    marked_at: datetime
    marked_by_user_id: str


class AttendanceSummary(BaseModel):
    event_id: str
    total_expected: int
    total_present: int
    attendance_rate: float
    present_members: list[dict]
    absent_members: list[dict]


class HeadcountEntry(BaseModel):
    event_id: str | None = None
    adults: int = Field(ge=0, le=1_000_000)
    children: int = Field(ge=0, le=1_000_000)
    total: int = Field(ge=0, le=2_000_000)


class PublicEvent(BaseModel):
    id: str
    title: str
    type: str
    description: str | None
    location: str | None
    online_url: str | None
    start_date_time: datetime
    end_date_time: datetime | None
    next_occurrence: datetime | None
    is_recurring: bool
    cover_image_url: str | None
    theme: EventTheme
