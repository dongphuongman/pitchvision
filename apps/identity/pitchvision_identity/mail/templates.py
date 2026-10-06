"""Plain text + minimal HTML bodies. Links point at the web paths in the contract."""

from html import escape
from urllib.parse import quote

from pitchvision_identity.mail.senders import EmailMessage


def _html(title: str, lines: list[str], link: str, button: str) -> str:
    paras = "".join(f"<p>{escape(line)}</p>" for line in lines)
    href = escape(link, quote=True)
    return (
        '<!doctype html><html><body style="font-family:system-ui,sans-serif;max-width:560px;margin:auto">'
        f"<h2>{escape(title)}</h2>{paras}"
        f'<p><a href="{href}" style="display:inline-block;padding:10px 18px;background:#16a34a;color:#fff;'
        f'text-decoration:none;border-radius:6px">{escape(button)}</a></p>'
        f'<p style="color:#666;font-size:13px">Or paste this link into your browser:<br>{escape(link)}</p>'
        "</body></html>"
    )


def _msg(to: str, subject: str, title: str, lines: list[str], link: str, button: str) -> EmailMessage:
    text = "\n\n".join([title, *lines, f"{button}: {link}"]) + "\n"
    return EmailMessage(to=to, subject=subject, text=text, html=_html(title, lines, link, button), link=link)


def verify_email(web_url: str, to: str, name: str, token: str) -> EmailMessage:
    link = f"{web_url}/verify-email?token={quote(token)}"
    return _msg(
        to,
        "Verify your email for PitchVision",
        f"Hi {name},",
        ["Confirm your email address to finish setting up PitchVision.", "This link expires in 24 hours."],
        link,
        "Verify email",
    )


def reset_email(web_url: str, to: str, name: str, token: str) -> EmailMessage:
    link = f"{web_url}/reset-password?token={quote(token)}"
    return _msg(
        to,
        "Reset your PitchVision password",
        f"Hi {name},",
        [
            "Someone asked to reset the password for this account. If it was not you, ignore this email.",
            "This link expires in 30 minutes.",
        ],
        link,
        "Choose a new password",
    )


def invite_email(
    web_url: str, to: str, inviter: str, org_name: str, role: str, token: str, message: str | None
) -> EmailMessage:
    link = f"{web_url}/invite/{quote(token)}"
    lines = [f"{inviter} invited you to join {org_name} on PitchVision as {role}."]
    if message:
        lines.append(f"“{message}”")
    lines.append("This invitation expires in 7 days.")
    return _msg(
        to, f"{inviter} invited you to {org_name} on PitchVision", "You're invited", lines, link, "Accept invitation"
    )


# SCR-51 notification emails (M1-BE-19): type → (subject, headline, body, button); {title} = the video's title
NOTIFICATION_COPY: dict[str, tuple[str, str, str, str]] = {
    "job_ready": ("{title} is ready", "Your analysis is ready", "{title} has been analysed. Open it to watch the "
                  "tracking and the tactical map.", "Open analysis"),
    "needs_teams": ("Confirm the teams for {title}", "One step left: confirm the teams",
                    "{title} has been analysed. Check the team names and colours so the stats are labelled right.",
                    "Confirm teams"),
    "job_failed": ("Analysis of {title} failed", "The analysis didn't finish",
                   "We couldn't finish analysing {title}. Open it to see what went wrong and retry.", "See details"),
    "export_ready": ("Your export {filename} is ready", "Your export is ready",
                     "{filename} is ready to download. The file is kept for 7 days.", "Download"),
    "quota_100": ("You've used this month's analysis minutes", "Monthly minutes used up",
                  "Your organization has used all {limit} analysis minutes this month. Upgrade to keep analysing.",
                  "See plans"),
}


def notification_email(web_url: str, to: str, name: str, type_: str, path: str, title: str | None,
                       data: dict[str, object]) -> EmailMessage | None:  # fmt: skip
    copy = NOTIFICATION_COPY.get(type_)
    if copy is None:
        return None
    values = {"title": title or "Your video", "filename": str(data.get("filename") or "your file"),
              "limit": str(data.get("limit") or "")}
    subject, headline, body, button = (part.format(**values) for part in copy)
    return _msg(to, subject, f"Hi {name},", [headline + ".", body], f"{web_url}{path}", button)
