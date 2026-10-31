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


def _same_kind(start: datetime | None, end: datetime | None) -> None:
    """Both times carry a UTC offset or neither does: a mix can't be compared or placed."""
    if start is not None and end is not None and (start.tzinfo is None) != (end.tzinfo is None):
        raise ValueError("Give the start and end both with or both without a time zone offset")


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
    is_all_day: bool = False


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
    is_all_day: bool = False
    #: The next date that has not finished (None for a one-off in the past or an ended series).
    next_occurrence: datetime | None = None
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
    is_all_day: bool = False
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
        _same_kind(self.start_date_time, self.end_date_time)
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
    is_all_day: bool | None = None
    #: The `updated_at` the editor was looking at. If someone saved since, the
    #: edit is refused (409 edit_conflict) instead of silently overwriting them.
    expected_updated_at: datetime | None = None

    @model_validator(mode="after")
    def _no_clearing_required_fields(self) -> "ChurchEventUpdate":
        for name in ("title", "type", "start_date_time", "is_all_day", "visibility", "attendance_mode"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be cleared")
        _same_kind(self.start_date_time, self.end_date_time)
        if self.start_date_time and self.end_date_time and self.end_date_time <= self.start_date_time:
            raise ValueError("The event must end after it starts")
        return self


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
    is_all_day: bool = False
    title: str
    type: str
    description: str | None
    location: str | None
    online_url: str | None
    start_date_time: datetime
    end_date_time: datetime | None
    next_occurrence: datetime | None
    is_recurring: bool
    #: How it repeats (an RRULE), so the public page can say "Every Sunday".
    recurrence_rule: str | None = None
    cover_image_url: str | None
    theme: EventTheme


# ───────────────────────────── Schedule collisions ─────────────────────────────


class CollisionCheckInput(BaseModel):
    group_id: str
    start_date_time: datetime
    end_date_time: datetime | None = None
    #: Set when checking an event that already exists, so it doesn't collide with itself.
    exclude_event_id: str | None = None


class KeyParticipant(BaseModel):
    member_id: str
    name: str
    role_name: str
    #: Which of the two groups in conflict they lead.
    group_name: str


class EventCollision(BaseModel):
    event_id: str
    event_title: str
    group_id: str
    group_name: str
    start_date_time: datetime
    end_date_time: datetime | None
    #: People in both groups — an approximation: it counts shared group
    #: membership, not RSVPs, since nothing here tracks who actually intends
    #: to come to either gathering.
    affected_member_count: int
    #: Group leaders among the affected members — the people this scheduling
    #: clash actually costs something, since they're expected to lead one of
    #: the two gatherings.
    key_participants: list[KeyParticipant]


class NotifyKeyParticipantsInput(BaseModel):
    event_id: str
    colliding_event_id: str
    member_ids: list[str]


# ───────────────────────────── Registration forms ─────────────────────────────

RegistrationFieldType = Literal["text", "single_choice", "checkbox"]
FieldLabel = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
FieldOption = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]


class RegistrationFieldInput(BaseModel):
    label: FieldLabel
    field_type: RegistrationFieldType
    options: list[FieldOption] = Field(default_factory=list, max_length=20)
    required: bool = False

    @model_validator(mode="after")
    def _choice_types_need_options(self):
        if self.field_type in ("single_choice", "checkbox") and not self.options:
            raise ValueError("Choice fields need at least one option")
        if self.field_type == "text" and self.options:
            raise ValueError("Text fields don't take options")
        return self


class RegistrationFieldRead(RegistrationFieldInput):
    id: str
    position: int


class SetRegistrationFieldsInput(BaseModel):
    #: The whole form, in display order — replaces whatever was there before.
    fields: list[RegistrationFieldInput] = Field(default_factory=list, max_length=30)


class PublicEventDetail(PublicEvent):
    registration_fields: list[RegistrationFieldRead]


class EventRegistrationInput(BaseModel):
    respondent_name: Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
    respondent_email: Annotated[str, StringConstraints(max_length=320)] | None = None
    respondent_phone: Annotated[str, StringConstraints(max_length=40)] | None = None
    #: Keyed by the field's label, not its id — see EventRegistrationField in
    #: schema.prisma for why. Value is a string for text/single_choice, a
    #: list of strings for checkbox.
    answers: dict[str, str | list[str]] = Field(default_factory=dict)


class EventRegistrationRead(BaseModel):
    id: str
    respondent_name: str
    respondent_email: str | None
    respondent_phone: str | None
    answers: dict[str, str | list[str]]
    created_at: datetime


# ───────────────────────────── Calendar window ─────────────────────────────


class CalendarOccurrence(BaseModel):
    """One dated appearance of an event. `occurrence_start` (with `event_id`)
    identifies it; times are UTC instants, shown in the church's time zone."""

    event_id: str
    title: str
    type: str
    status: str
    visibility: Visibility
    unit_id: str | None
    group_id: str | None
    group_name: str | None
    location: str | None
    occurrence_start: datetime
    occurrence_end: datetime | None
    is_all_day: bool
    is_recurring: bool
    accent_color: str | None = None


class CalendarWindow(BaseModel):
    #: The church's IANA zone (UTC if none is set): how to place events on a day.
    timezone: str
    window_start: datetime
    window_end: datetime
    #: True when more occurrences exist than were returned; narrow the window or filters.
    truncated: bool
    occurrences: list[CalendarOccurrence]


class PublicCalendarOccurrence(BaseModel):
    event_id: str
    title: str
    type: str
    location: str | None
    occurrence_start: datetime
    occurrence_end: datetime | None
    is_all_day: bool
    is_recurring: bool
    accent_color: str | None = None


class PublicCalendarWindow(BaseModel):
    timezone: str
    window_start: datetime
    window_end: datetime
    truncated: bool
    occurrences: list[PublicCalendarOccurrence]


class EventHistoryEntry(BaseModel):
    id: str
    action: str
    actor_name: str | None
    created_at: datetime
    metadata: dict | None = None
