"""Forms: definitions, questions and submissions.

Two audiences use this module:

- the forms router, for standalone forms (a church builds one, shares its link);
- other modules, for forms they own. Events keeps an event's registration form
  here through the ``owned_*`` functions below, which are the whole contract: an
  owner is an opaque ``(kind, id)`` pair, the caller has already decided who may
  act on it, and this module reads none of the owner's tables.

A submission is add-only and carries the questions it answered (``form_snapshot``),
so editing a form later never changes what someone was asked or said."""

import csv
import io
import json
import re
from datetime import UTC, datetime

from prisma import Prisma

from app.core.database import set_tenant_context
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.timezones import church_tz
from app.modules.audit.service import write_audit
from app.modules.forms import repository
from app.modules.notifications import service as notifications_service
from app.modules.tenant import service as tenant_service

OWNER_EVENT = "event"
OWNER_KINDS = frozenset({OWNER_EVENT})

#: One export is held in memory and handed over whole.
MAX_EXPORT_ROWS = 10_000

_TRANSITIONS = {"draft": {"published"}, "published": {"closed"}, "closed": {"published"}}
_AUDIT_RESOURCE = "form"


class FormClosedError(ConflictError):
    code = "form_closed"


def _json(value):
    """A JSON column from a raw query: already parsed, or a string to parse."""
    return json.loads(value) if isinstance(value, str) else value


def _dt(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _field_dict(f: dict) -> dict:
    return {
        "label": f["label"],
        "field_type": f["field_type"],
        "options": list(f.get("options") or []),
        "required": bool(f.get("required")),
        "position": f.get("position", 0),
    }


def _submission(row: dict) -> dict:
    out = {**row, "answers": _json(row["answers"])}
    if "form_snapshot" in row:
        out["form_snapshot"] = _json(row["form_snapshot"])
    return out


async def _audit(db: Prisma, tenant_id: str, actor: str | None, action: str, form: dict, **metadata) -> None:
    if form.get("owner_kind"):
        metadata = {"owner_kind": form["owner_kind"], "owner_id": str(form["owner_id"]), **metadata}
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action=action,
        resource_type=_AUDIT_RESOURCE,
        resource_id=str(form["id"]),
        metadata=metadata or None,
    )


# ── Validation ───────────────────────────────────────────────────────────


def _is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and not value)


def clean_answers(fields: list[dict], answers: dict) -> dict:
    """Check what someone sent against the form's questions and return what to
    keep. Server-side and strict: only the form's own questions, only its own
    options, the right shape for each type, and required means required. An
    unanswered optional question is simply absent."""
    by_label = {f["label"]: f for f in fields}
    unknown = [label for label in answers if label not in by_label]
    if unknown:
        raise AppError("That form has no such question", code="unknown_question")
    cleaned: dict = {}
    for field in fields:
        label, kind = field["label"], field["field_type"]
        value = answers.get(label)
        if _is_empty(value):
            if field["required"]:
                raise AppError(f'"{label}" is required', code="answer_required")
            continue
        options = field.get("options") or []
        if kind == "text":
            if not isinstance(value, str):
                raise AppError(f'"{label}" needs a written answer', code="answer_invalid")
            cleaned[label] = value.strip()
        elif kind == "single_choice":
            if not isinstance(value, str) or value not in options:
                raise AppError(f'Choose one of the options for "{label}"', code="answer_invalid")
            cleaned[label] = value
        else:
            if not isinstance(value, list) or any(v not in options for v in value):
                raise AppError(f'Choose from the options for "{label}"', code="answer_invalid")
            cleaned[label] = list(dict.fromkeys(value))
    return cleaned


def _snapshot(form: dict, fields: list[dict]) -> dict:
    return {"form_title": form["title"], "fields": [_field_dict(f) for f in fields]}


# ── Reading a form ───────────────────────────────────────────────────────


async def _full(db: Prisma, form: dict) -> dict:
    return {**form, "fields": await repository.list_fields(db, str(form["id"]))}


async def _standalone(db: Prisma, form_id: str) -> dict:
    form = await repository.get_form(db, form_id)
    if form is None or form["owner_kind"] is not None:
        raise NotFoundError("No such form")
    return form


async def list_forms(db: Prisma, status: str | None = None) -> list[dict]:
    return await repository.list_standalone(db, status)


async def get_form(db: Prisma, form_id: str) -> dict:
    return await _full(db, await _standalone(db, form_id))


# ── Standalone forms: managing ───────────────────────────────────────────


async def create_form(db: Prisma, tenant_id: str, actor_user_id: str, payload: dict, unit_id: str | None) -> dict:
    form_id = await repository.create_form(
        db,
        tenant_id,
        title=payload["title"],
        description=payload.get("description"),
        unit_id=unit_id,
        created_by_user_id=actor_user_id,
    )
    await repository.replace_fields(db, tenant_id, form_id, payload.get("fields") or [])
    form = await _standalone(db, form_id)
    await _audit(db, tenant_id, actor_user_id, "form.created", form, fields=len(payload.get("fields") or []))
    return await _full(db, form)


async def update_form(db: Prisma, tenant_id: str, actor_user_id: str, form_id: str, changes: dict) -> dict:
    form = await _standalone(db, form_id)
    await repository.update_form(db, form_id, changes)
    await _audit(db, tenant_id, actor_user_id, "form.updated", form, changed=sorted(changes))
    return await get_form(db, form_id)


async def set_fields(db: Prisma, tenant_id: str, actor_user_id: str, form_id: str, fields: list[dict]) -> list[dict]:
    form = await _standalone(db, form_id)
    saved = await _replace_fields(db, tenant_id, form, fields, actor_user_id)
    return saved


async def _replace_fields(db: Prisma, tenant_id: str, form: dict, fields: list[dict], actor: str | None) -> list[dict]:
    saved = await repository.replace_fields(db, tenant_id, str(form["id"]), fields)
    await repository.touch(db, str(form["id"]))
    await _audit(db, tenant_id, actor, "form.fields_changed", form, fields=len(saved))
    return saved


async def _move(db: Prisma, tenant_id: str, actor: str | None, form: dict, target: str) -> dict:
    if form["status"] == target:
        return form
    if target not in _TRANSITIONS[form["status"]]:
        raise ConflictError(f"A {form['status']} form can't be made {target}", code="invalid_transition")
    await repository.set_status(db, str(form["id"]), target)
    action = "form.reopened" if form["status"] == "closed" else f"form.{target}"
    await _audit(db, tenant_id, actor, action, form)
    return {**form, "status": target}


async def set_status(db: Prisma, tenant_id: str, actor_user_id: str, form_id: str, target: str) -> dict:
    await _move(db, tenant_id, actor_user_id, await _standalone(db, form_id), target)
    return await get_form(db, form_id)


async def delete_form(db: Prisma, tenant_id: str, actor_user_id: str, form_id: str) -> None:
    form = await _standalone(db, form_id)
    if form["submission_count"]:
        # Answers are a record of what people said; a form that has some is closed, not deleted.
        raise ConflictError("This form has responses. Close it instead of deleting it.", code="has_submissions")
    await repository.delete_form(db, form_id)
    await _audit(db, tenant_id, actor_user_id, "form.deleted", form)


# ── Submissions: reading (staff) ─────────────────────────────────────────


async def list_submissions(db: Prisma, form_id: str) -> list[dict]:
    await _standalone(db, form_id)
    return [_submission(r) for r in await repository.list_submissions(db, form_id)]


async def get_submission(db: Prisma, tenant_id: str, actor_user_id: str, form_id: str, submission_id: str) -> dict:
    form = await _standalone(db, form_id)
    row = await repository.get_submission(db, form_id, submission_id)
    if row is None:
        raise NotFoundError("No such response")
    # Opening one person's answers is reading their personal details: on the record.
    await _audit(db, tenant_id, actor_user_id, "form.submission_read", form, submission_id=submission_id)
    return _submission(row)


# ── CSV ──────────────────────────────────────────────────────────────────

_NUMBERISH = re.compile(r"[+-]?[0-9 ().-]+")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    """One CSV cell. People type these answers on a public page, so a cell that a
    spreadsheet would run as a formula ("=HYPERLINK(...)") is made plain text with a
    leading apostrophe; plain numbers and phone numbers are left alone."""
    if value is None:
        return ""
    text = ", ".join(value) if isinstance(value, list) else str(value)
    if text.startswith(_FORMULA_START) and not _NUMBERISH.fullmatch(text):
        text = "'" + text
    return text


def build_csv(fields: list[dict], rows: list[dict], zone) -> str:
    """Name, contact, time, then one column per question: the form's current
    questions in their order, then any question since removed (its answers were
    kept, so its column is too), in the order it had when it was answered."""
    labels = [f["label"] for f in fields]
    for row in rows:
        asked = [f["label"] for f in (row.get("form_snapshot") or {}).get("fields", [])]
        for label in asked + sorted(k for k in row["answers"] if k not in asked):
            if label in row["answers"] and label not in labels:
                labels.append(label)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["Name", "Email", "Phone", "Submitted", *labels])
    for row in rows:
        when = _dt(row["created_at"]).astimezone(zone).strftime("%Y-%m-%d %H:%M")
        writer.writerow(
            [
                _cell(row["respondent_name"]),
                _cell(row["respondent_email"]),
                _cell(row["respondent_phone"]),
                when,
                *[_cell(row["answers"].get(label)) for label in labels],
            ]
        )
    # The BOM makes Excel read the file as UTF-8 instead of mangling accented names.
    return "\ufeff" + out.getvalue()


async def _export(db: Prisma, tenant_id: str, actor: str, form: dict | None, title: str) -> tuple[str, str]:
    zone = church_tz(await repository.church_timezone(db))
    stem = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "form"
    filename = f"{stem}-responses-{datetime.now(UTC).date().isoformat()}.csv"
    if form is None:  # an owner that never had a form: a file with just the headings
        return filename, build_csv([], [], zone)
    rows = [
        _submission(r) for r in await repository.submissions_with_snapshots(db, str(form["id"]), MAX_EXPORT_ROWS + 1)
    ]
    if len(rows) > MAX_EXPORT_ROWS:
        raise AppError(f"Too many responses to export at once (more than {MAX_EXPORT_ROWS})", code="export_too_large")
    fields = await repository.list_fields(db, str(form["id"]))
    await _audit(db, tenant_id, actor, "form.submissions_exported", form, count=len(rows))
    return filename, build_csv(fields, rows, zone)


async def export_submissions(db: Prisma, tenant_id: str, actor_user_id: str, form_id: str) -> tuple[str, str]:
    form = await _standalone(db, form_id)
    return await _export(db, tenant_id, actor_user_id, form, form["title"])


# ── Submitting ───────────────────────────────────────────────────────────


async def _accept(
    db: Prisma,
    tenant_id: str,
    form: dict,
    payload: dict,
    *,
    notify_user_ids: list[str],
    notify_link: str,
    notify_type: str,
) -> dict:
    if form["status"] != "published":
        raise FormClosedError("This form isn't taking responses")
    fields = await repository.list_fields(db, str(form["id"]))
    answers = clean_answers(fields, payload.get("answers") or {})
    saved = await repository.create_submission(
        db, tenant_id, str(form["id"]), {**payload, "answers": answers}, _snapshot(form, fields)
    )
    if notify_user_ids:
        # A pointer, not the response: names and answers stay behind the staff permission. One
        # a day per form per person (dedupe), so a busy form is a nudge, not a flood.
        await notifications_service.notify(
            db,
            tenant_id,
            notify_user_ids,
            notify_type,
            f"New response: {form['title']}",
            "Someone filled in this form. Open the responses to read it.",
            action_url=notify_link,
            resource=(_AUDIT_RESOURCE, str(form["id"])),
            dedupe_key=f"form.response:{form['id']}:{datetime.now(UTC):%Y%m%d}",
            email=False,
        )
    return _submission(saved)


async def _public_form(db: Prisma, slug: str, public_key: str) -> tuple[str, dict]:
    org = await tenant_service.get_live_org_by_slug(db, slug)
    await set_tenant_context(db, org.id)
    form = await repository.get_form_by_public_key(db, public_key)
    # Event forms are reached only through their event; a draft isn't public yet.
    if form is None or form["owner_kind"] is not None or form["status"] == "draft":
        raise NotFoundError("No such form")
    return org.id, form


async def public_form(db: Prisma, slug: str, public_key: str) -> dict:
    _, form = await _public_form(db, slug, public_key)
    return await _full(db, form)


async def submit_public(db: Prisma, slug: str, public_key: str, payload: dict) -> dict:
    tenant_id, form = await _public_form(db, slug, public_key)
    recipients = [str(form["created_by_user_id"])] if form["created_by_user_id"] else []
    return await _accept(
        db,
        tenant_id,
        form,
        payload,
        notify_user_ids=recipients,
        notify_link=f"/staff/forms/{form['id']}/responses",
        notify_type="system",
    )


# ── For modules that own a form ──────────────────────────────────────────
#
# The caller (events) has already checked that the person may act on the owner and
# that the owner can take sign-ups; these only do the forms part.


def _owner(kind: str, owner_id: str) -> tuple[str, str]:
    if kind not in OWNER_KINDS:
        raise ValueError(f"Unknown form owner {kind!r}")
    return kind, owner_id


async def owned_fields(db: Prisma, kind: str, owner_id: str) -> list[dict]:
    form = await repository.get_owned_form(db, *_owner(kind, owner_id))
    return await repository.list_fields(db, str(form["id"])) if form else []


async def owned_is_open(db: Prisma, kind: str, owner_id: str) -> bool:
    """Whether the owner's form takes answers. No form yet counts as open: an event
    with no questions is a plain "I'll be there"."""
    form = await repository.get_owned_form(db, *_owner(kind, owner_id))
    return form is None or form["status"] == "published"


async def owned_status(db: Prisma, kind: str, owner_id: str) -> str:
    form = await repository.get_owned_form(db, *_owner(kind, owner_id))
    return form["status"] if form else "published"


async def _owned_form(
    db: Prisma, tenant_id: str, kind: str, owner_id: str, *, title: str, unit_id: str | None, actor: str | None
) -> dict:
    return await repository.get_or_create_owned(
        db, tenant_id, _owner(kind, owner_id), title=title, unit_id=unit_id, created_by_user_id=actor
    )


async def set_owned_fields(
    db: Prisma,
    tenant_id: str,
    kind: str,
    owner_id: str,
    *,
    title: str,
    unit_id: str | None,
    fields: list[dict],
    actor_user_id: str,
) -> list[dict]:
    form = await _owned_form(db, tenant_id, kind, owner_id, title=title, unit_id=unit_id, actor=actor_user_id)
    return await _replace_fields(db, tenant_id, form, fields, actor_user_id)


async def set_owned_status(
    db: Prisma,
    tenant_id: str,
    kind: str,
    owner_id: str,
    target: str,
    *,
    title: str,
    unit_id: str | None,
    actor_user_id: str,
) -> str:
    form = await _owned_form(db, tenant_id, kind, owner_id, title=title, unit_id=unit_id, actor=actor_user_id)
    return (await _move(db, tenant_id, actor_user_id, form, target))["status"]


async def owned_submissions(db: Prisma, kind: str, owner_id: str) -> list[dict]:
    form = await repository.get_owned_form(db, *_owner(kind, owner_id))
    if form is None:
        return []
    return [_submission(r) for r in await repository.list_submissions(db, str(form["id"]))]


async def owned_respondent_emails(db: Prisma, kind: str, owner_id: str) -> list[str]:
    form = await repository.get_owned_form(db, *_owner(kind, owner_id))
    return await repository.respondent_emails(db, str(form["id"])) if form else []


async def export_owned(
    db: Prisma, tenant_id: str, actor_user_id: str, kind: str, owner_id: str, *, title: str
) -> tuple[str, str]:
    form = await repository.get_owned_form(db, *_owner(kind, owner_id))
    return await _export(db, tenant_id, actor_user_id, form, title)


async def submit_owned(
    db: Prisma,
    tenant_id: str,
    kind: str,
    owner_id: str,
    payload: dict,
    *,
    title: str,
    unit_id: str | None,
    notify_user_ids: list[str],
    notify_link: str,
    notify_type: str,
) -> dict:
    form = await _owned_form(db, tenant_id, kind, owner_id, title=title, unit_id=unit_id, actor=None)
    return await _accept(
        db, tenant_id, form, payload, notify_user_ids=notify_user_ids, notify_link=notify_link, notify_type=notify_type
    )
