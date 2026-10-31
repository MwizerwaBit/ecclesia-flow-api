from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

FieldType = Literal["text", "single_choice", "checkbox"]
FormStatus = Literal["draft", "published", "closed"]

Label = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
Option = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
Title = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]

MAX_FIELDS = 30
MAX_OPTIONS = 20
#: A text answer, in characters. Long enough for a paragraph, short enough that a
#: public form can't be used to store a document.
MAX_TEXT_ANSWER = 2000
MAX_ANSWERS = MAX_FIELDS + 10


class FieldInput(BaseModel):
    label: Label
    field_type: FieldType
    options: list[Option] = Field(default_factory=list, max_length=MAX_OPTIONS)
    required: bool = False

    @model_validator(mode="after")
    def _shape(self):
        if self.field_type in ("single_choice", "checkbox") and not self.options:
            raise ValueError("Choice fields need at least one option")
        if self.field_type == "text" and self.options:
            raise ValueError("Text fields don't take options")
        if len({o.casefold() for o in self.options}) != len(self.options):
            raise ValueError("Options must be different from each other")
        return self


class FieldRead(FieldInput):
    id: str
    position: int


def _distinct_labels(fields: list[FieldInput]) -> list[FieldInput]:
    # Answers are stored by label, so two questions with one label would share an answer.
    if len({f.label.casefold() for f in fields}) != len(fields):
        raise ValueError("Each question needs its own wording")
    return fields


class SetFieldsInput(BaseModel):
    #: The whole form, in display order: replaces whatever was there.
    fields: list[FieldInput] = Field(default_factory=list, max_length=MAX_FIELDS)

    _distinct = field_validator("fields")(_distinct_labels)


class FormCreate(BaseModel):
    title: Title
    description: Annotated[str, StringConstraints(max_length=2000, strip_whitespace=True)] | None = None
    unit_id: str | None = None
    fields: list[FieldInput] = Field(default_factory=list, max_length=MAX_FIELDS)

    _distinct = field_validator("fields")(_distinct_labels)


class FormUpdate(BaseModel):
    title: Title | None = None
    description: Annotated[str, StringConstraints(max_length=2000, strip_whitespace=True)] | None = None

    @model_validator(mode="after")
    def _something(self):
        if not self.model_fields_set:
            raise ValueError("Nothing to change")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("A form needs a title")
        return self


class FormRead(BaseModel):
    id: str
    title: str
    description: str | None
    status: FormStatus
    unit_id: str | None
    #: The unguessable part of the public link: /c/<church slug>/forms/<public_key>.
    public_key: str
    fields: list[FieldRead]
    submission_count: int
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    closed_at: datetime | None


class FormListItem(BaseModel):
    id: str
    title: str
    status: FormStatus
    unit_id: str | None
    submission_count: int
    created_at: datetime
    updated_at: datetime


class PublicFormRead(BaseModel):
    """What the share link shows. Nothing about the church's own data: no ids
    beyond the question ids, no branch, no counts, no author."""

    title: str
    description: str | None
    status: Literal["published", "closed"]
    fields: list[FieldRead]


Answer = Annotated[str, StringConstraints(max_length=MAX_TEXT_ANSWER)]


class SubmissionInput(BaseModel):
    respondent_name: Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
    respondent_email: Annotated[str, StringConstraints(max_length=320, strip_whitespace=True)] | None = None
    respondent_phone: Annotated[str, StringConstraints(max_length=40, strip_whitespace=True)] | None = None
    #: Keyed by the question's label. A string for text and single choice, a list
    #: of strings for checkbox.
    answers: dict[Annotated[str, StringConstraints(max_length=200)], Answer | list[Option]] = Field(
        default_factory=dict, max_length=MAX_ANSWERS
    )

    @field_validator("respondent_email")
    @classmethod
    def _email(cls, value: str | None):
        if not value:
            return None
        local, _, domain = value.partition("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith(".") or " " in value:
            raise ValueError("That doesn't look like an email address")
        return value

    @field_validator("respondent_phone")
    @classmethod
    def _phone(cls, value: str | None):
        if not value:
            return None
        if not all(c.isdigit() or c in "+-() ." for c in value) or sum(c.isdigit() for c in value) < 5:
            raise ValueError("That doesn't look like a phone number")
        return value


class SnapshotField(BaseModel):
    label: str
    field_type: FieldType
    options: list[str] = Field(default_factory=list)
    required: bool = False
    position: int = 0


class FormSnapshot(BaseModel):
    form_title: str
    fields: list[SnapshotField]
    #: True for a submission copied from the old registration tables: its questions
    #: are the ones at the time of the copy, not necessarily when it was sent.
    reconstructed: bool = False


class SubmissionRead(BaseModel):
    id: str
    respondent_name: str
    respondent_email: str | None
    respondent_phone: str | None
    answers: dict[str, str | list[str]]
    created_at: datetime


class SubmissionDetail(SubmissionRead):
    """One submission with the questions as they were when it was sent."""

    form_snapshot: FormSnapshot
