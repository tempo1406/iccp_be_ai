from __future__ import annotations

from .domains.auth.api import BeCoreAuthApi
from .domains.billing.api import BeCoreBillingApi
from .domains.daily_reports.api import DailyReportsApi
from .domains.documents.api import DocumentsApi
from .domains.notifications.api import BeCoreNotificationsApi
from .domains.organizations.api import OrganizationsApi
from .domains.projects.api import ProjectsApi
from .domains.tasks.api import TasksApi
from .domains.tickets.api import TicketsApi


class BeCoreClient(
    BeCoreAuthApi,
    BeCoreBillingApi,
    BeCoreNotificationsApi,
    DailyReportsApi,
    DocumentsApi,
    OrganizationsApi,
    ProjectsApi,
    TasksApi,
    TicketsApi,
):
    """Facade client that keeps existing BeCoreClient API stable."""
