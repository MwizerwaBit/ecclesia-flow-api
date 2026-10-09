from datetime import date, datetime

from pydantic import BaseModel


class FundRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None
    is_default: bool
    target: float | None
    total_received: float
    is_active: bool
    created_at: datetime


class FundCreate(BaseModel):
    name: str
    description: str | None = None
    is_default: bool = False
    target: float | None = None


class DonationBatchRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None
    service_id: str | None
    service_name: str | None = None
    date: date
    status: str
    total_amount: float
    donation_count: int
    verified_total: float | None
    created_by_user_id: str
    created_by_name: str | None = None
    closed_at: datetime | None
    posted_at: datetime | None
    created_at: datetime


class DonationBatchCreate(BaseModel):
    name: str
    description: str | None = None
    service_id: str | None = None
    date: date


class CloseBatchInput(BaseModel):
    verified_total: float


class DonationRead(BaseModel):
    id: str
    batch_id: str
    tenant_id: str
    member_id: str | None
    member_name: str | None = None
    envelope_number: str | None
    is_guest: bool
    guest_name: str | None
    fund_id: str
    fund_name: str | None = None
    amount: float
    payment_method: str
    notes: str | None
    reference_number: str | None
    is_voided: bool
    voided_at: datetime | None
    void_reason: str | None
    voided_by_user_id: str | None
    created_at: datetime
    created_by_user_id: str


class DonationEntryForm(BaseModel):
    mode: str  # 'envelope' | 'member_search'
    envelope_number: str | None = None
    member_id: str | None = None
    is_guest: bool = False
    guest_name: str | None = None
    fund_id: str
    amount: float
    payment_method: str
    notes: str | None = None


class VoidDonationInput(BaseModel):
    reason: str


class PledgeRead(BaseModel):
    id: str
    tenant_id: str
    member_id: str
    member_name: str | None = None
    fund_id: str
    fund_name: str | None = None
    pledge_amount: float
    amount_fulfilled: float
    start_date: date
    end_date: date
    frequency: str | None
    status: str
    created_at: datetime


class FinanceDashboard(BaseModel):
    this_week_total: float
    last_week_total: float
    open_batch_count: int
    pledge_progress: float
    year_to_date_total: float
    last_year_total: float
    fund_breakdown: list[dict]


class GivingReportFundBreakdown(BaseModel):
    fund_id: str
    fund_name: str
    amount: float
    percentage: float


class GivingReportTrendPoint(BaseModel):
    label: str
    amount: float


class GivingReport(BaseModel):
    period: str
    start_date: date
    end_date: date
    total_amount: float
    donation_count: int
    by_fund: list[GivingReportFundBreakdown]
    trend: list[GivingReportTrendPoint]


class StatementLine(BaseModel):
    date: datetime
    fund_name: str
    amount: float
    payment_method: str
    reference_number: str | None


class ContributionStatement(BaseModel):
    member_id: str
    member_name: str
    year: int
    total_amount: float
    donations: list[StatementLine]
    generated_at: datetime
