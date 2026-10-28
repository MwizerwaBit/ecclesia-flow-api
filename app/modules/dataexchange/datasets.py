"""The datasets that can be exported and imported, and their columns.

One list drives everything: the template a church downloads, the header of an
export (so an export is always a valid import file), and the guide the screen
shows. Adding a dataset means adding it here and teaching service.py to read
and write it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    help: str = ""
    example: str = ""
    required: bool = False


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    description: str
    columns: list[Column]
    import_permission: str
    export_permission: str


DATE_HELP = "Year-month-day (2024-03-31) or day/month/year (31/03/2024)"

MEMBER_COLUMNS = [
    Column("first_name", "First name", "", "Jean", required=True),
    Column("last_name", "Last name", "", "Mukiza", required=True),
    Column("gender", "Gender", "male or female", "male", required=True),
    Column("status", "Status", "active, visitor, prospect or inactive. Empty means active", "active"),
    Column("phone", "Phone", "A phone or an email is required", "+250788123456"),
    Column("email", "Email", "A phone or an email is required", "jean@example.org"),
    Column("national_id", "ID number", "Required for adult members (active or inactive)", "1199880012345678"),
    Column("id_type", "ID type", "national_id, passport or other. Empty means national_id", "national_id"),
    Column("date_of_birth", "Date of birth", DATE_HELP, "1988-05-17"),
    Column("title", "Title", "Mr, Mrs, Dr, Rev…", "Mr"),
    Column("middle_name", "Middle name"),
    Column("preferred_name", "Preferred name"),
    Column("whatsapp", "WhatsApp number"),
    Column("marital_status", "Marital status", "single, married, widowed, divorced or separated", "married"),
    Column("occupation", "Occupation", "", "Teacher"),
    Column("employer", "Employer"),
    Column("address_line1", "Address", "Street or village. If you give an address you need city and country too"),
    Column("address_city", "City"),
    Column("address_state", "Province / state"),
    Column("address_country", "Country"),
    Column("address_postal_code", "Postal code"),
    Column("joined_at", "Date joined", DATE_HELP),
    Column(
        "join_method",
        "How they joined",
        "first_visit, transfer, baptism, profession_of_faith, born_into or other",
    ),
    Column("is_baptised", "Baptised", "yes or no"),
    Column("baptism_date", "Baptism date", DATE_HELP),
    Column("envelope_number", "Envelope number", "Digits only, one per person"),
    Column(
        "household_name", "Household", "People with the same household name are put in one household", "Mukiza family"
    ),
    Column("household_role", "Household role", "head, spouse, child, relative or other"),
    Column("groups", "Groups", "Names of groups that already exist, separated by ;", "Choir; Youth"),
    Column("emergency_contact_name", "Emergency contact name"),
    Column("emergency_contact_phone", "Emergency contact phone"),
    Column("emergency_contact_relationship", "Emergency contact relationship"),
    Column("consent_communications", "Agreed to messages", "yes or no. Empty means no"),
    Column("notes", "Notes"),
]

MEMBERS = Dataset(
    key="members",
    label="Members and visitors",
    description="Everyone in the directory: contact details, household, groups and consent.",
    columns=MEMBER_COLUMNS,
    import_permission="members:create",
    export_permission="members:export",
)

DATASETS: dict[str, Dataset] = {d.key: d for d in [MEMBERS]}
