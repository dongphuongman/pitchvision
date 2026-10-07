"""In-app + email notifications (SCR-51, M1-BE-19). Rows live in the regional DB (``notifications``, API migration
0006); the worker / API insert them, the API serves them, identity-svc sends the emails (it owns addresses).

``path`` is the web route a notification opens (SCR-51 E02) — shared so the in-app list, the email link and the
web client agree.
"""

from typing import Literal

NotificationType = Literal["job_ready", "needs_teams", "job_failed", "export_ready", "export_failed", "quota_80",
                           "quota_100", "data_export_ready", "member_joined"]  # fmt: skip
NOTIFICATION_TYPES: tuple[NotificationType, ...] = ("job_ready", "needs_teams", "job_failed", "export_ready",
                                                    "export_failed", "quota_80", "quota_100",
                                                    "data_export_ready", "member_joined")  # fmt: skip
# also sent by email (the rest are in-app only)
EMAIL_TYPES: frozenset[str] = frozenset({"job_ready", "needs_teams", "job_failed", "export_ready", "quota_100",
                                         "data_export_ready"})  # fmt: skip


def notification_path(type_: str, video_id: str | None) -> str:
    """SCR-51 E02 destinations: ready → SCR-20, failed → SCR-13, needs teams → SCR-14, export → SCR-26,
    quota → SCR-40 billing, data export → SCR-40 Data & privacy, member joined →
    SCR-40 Members."""
    if video_id and type_ == "job_ready":
        return f"/videos/{video_id}"
    if video_id and type_ == "needs_teams":
        return f"/videos/{video_id}/teams"
    if video_id and type_ == "job_failed":
        return f"/videos/{video_id}/processing"
    if type_ in ("export_ready", "export_failed"):
        return "/exports"
    if type_ in ("quota_80", "quota_100"):
        return "/settings/billing"
    if type_ == "data_export_ready":
        return "/settings/privacy"
    if type_ == "member_joined":
        return "/settings/members"
    return "/library"
