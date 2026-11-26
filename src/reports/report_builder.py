
import html
import json

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ACTIONS = ("like", "reply", "repost")
ACTION_LABELS = {
    "like": "Like",
    "reply": "Reply",
    "repost": "Repost",
}


def _load_json(path):
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def _parse_datetime(value):
    if not value or not isinstance(value, str):
        return None

    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=ZoneInfo("UTC"))

        return parsed
    except ValueError:
        return None


def _local_date(value, timezone):
    parsed = _parse_datetime(value)

    if parsed is None:
        return None

    return parsed.astimezone(timezone).date()


def _post_url(post, post_id):
    url = post.get("url")

    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return url

    return f"https://x.com/i/status/{post_id}"


def _reply_url(external_id):
    if not external_id:
        return None

    value = str(external_id)

    if value.startswith(("https://", "http://")):
        return value

    return f"https://x.com/i/status/{value}"


def _action_record(post_id, post, action_name, action_data):
    original_url = _post_url(post, post_id)
    external_id = action_data.get("external_id")

    record = {
        "action": action_name,
        "label": ACTION_LABELS[action_name],
        "post_id": str(post_id),
        "author": post.get("author") or post.get("username") or "Autor desconocido",
        "text": post.get("text") or "",
        "original_url": original_url,
        "completed_at": action_data.get("completed_at"),
        "action_url": original_url,
        "reply_url": None,
        "payload": action_data.get("payload") or {},
    }

    if action_name == "reply":
        record["reply_url"] = _reply_url(external_id)
        record["action_url"] = record["reply_url"] or original_url

    return record


def build_daily_report(
    tracker_path="data/processed_posts.json",
    config_path="config/x_actions.json",
    report_date=None,
):
    """
    Build a daily report using confirmed actions from the tracker.

    Only actions with status='completed' and a completed_at timestamp
    belonging to the requested local calendar day are counted.
    """

    config = _load_json(config_path)
    timezone_name = config.get("timezone")

    if not timezone_name:
        raise ValueError("Missing timezone in config/x_actions.json")

    local_timezone = ZoneInfo(timezone_name)

    if report_date is None:
        report_date = datetime.now(local_timezone).date() - timedelta(days=1)
    elif isinstance(report_date, str):
        report_date = date.fromisoformat(report_date)

    tracker = _load_json(tracker_path)
    posts = tracker.get("posts", {})

    if not isinstance(posts, dict):
        raise ValueError("Invalid tracker structure: 'posts' must be an object")

    actions_by_type = {action: [] for action in ACTIONS}
    issues = []

    for post_id, post in posts.items():
        if not isinstance(post, dict):
            continue

        post_actions = post.get("actions", {})

        if not isinstance(post_actions, dict):
            continue

        for action_name in ACTIONS:
            action_data = post_actions.get(action_name, {})

            if not isinstance(action_data, dict):
                continue

            status = action_data.get("status")
            completed_at = action_data.get("completed_at")

            if status == "completed":
                if _local_date(completed_at, local_timezone) == report_date:
                    actions_by_type[action_name].append(
                        _action_record(
                            post_id,
                            post,
                            action_name,
                            action_data,
                        )
                    )

            elif status in ("failed", "unknown"):
                attempted_at = action_data.get("last_attempt_at")

                if _local_date(attempted_at, local_timezone) == report_date:
                    issues.append({
                        "action": action_name,
                        "label": ACTION_LABELS[action_name],
                        "post_id": str(post_id),
                        "author": (
                            post.get("author")
                            or post.get("username")
                            or "Autor desconocido"
                        ),
                        "text": post.get("text") or "",
                        "original_url": _post_url(post, post_id),
                        "status": status,
                        "attempted_at": attempted_at,
                        "error": action_data.get("error"),
                    })

    for action_name in ACTIONS:
        actions_by_type[action_name].sort(
            key=lambda item: item.get("completed_at") or ""
        )

    issues.sort(key=lambda item: item.get("attempted_at") or "")

    totals = {
        action_name: len(records)
        for action_name, records in actions_by_type.items()
    }

    return {
        "date": report_date.isoformat(),
        "timezone": timezone_name,
        "totals": totals,
        "total_interactions": sum(totals.values()),
        "failed_or_unknown": len(issues),
        "actions": actions_by_type,
        "issues": issues,
    }


def render_html(report):
    """Render the daily report as a self-contained HTML email."""

    report_date = html.escape(report["date"])
    totals = report["totals"]

    def action_section(action_name, records):
        label = ACTION_LABELS[action_name]

        if not records:
            return f"""
                <h2>{label}s</h2>
                <p class="muted">No hubo {label.lower()}s confirmados.</p>
            """

        rows = []

        for item in records:
            author = html.escape(str(item["author"]))
            post_text = html.escape(str(item["text"]))
            original_url = html.escape(item["original_url"], quote=True)
            action_url = html.escape(item["action_url"], quote=True)
            completed_at = html.escape(str(item["completed_at"] or ""))

            links = f'<a href="{original_url}">Ver post original</a>'

            if action_name == "reply" and item.get("reply_url"):
                links += (
                    f' &nbsp;·&nbsp; '
                    f'<a href="{action_url}">Ver reply publicado</a>'
                )

            rows.append(f"""
                <tr>
                    <td>
                        <strong>@{author.lstrip("@")}</strong>
                        <p class="post-text">{post_text}</p>
                        <p class="links">{links}</p>
                        <p class="muted small">{completed_at}</p>
                    </td>
                </tr>
            """)

        return f"""
            <h2>{label}s <span class="count">{len(records)}</span></h2>
            <table class="details">
                {''.join(rows)}
            </table>
        """

    sections = "".join(
        action_section(action_name, report["actions"][action_name])
        for action_name in ACTIONS
    )

    issue_rows = []

    for issue in report["issues"]:
        author = html.escape(str(issue["author"]))
        status = html.escape(str(issue["status"]).upper())
        error = html.escape(str(issue.get("error") or "Sin detalle"))
        url = html.escape(issue["original_url"], quote=True)

        issue_rows.append(f"""
            <tr>
                <td>
                    <strong>{issue["label"]}</strong> · @{author.lstrip("@")}
                    <span class="status">{status}</span>
                    <p class="muted small">{error}</p>
                    <a href="{url}">Ver post</a>
                </td>
            </tr>
        """)

    issues_section = ""

    if issue_rows:
        issues_section = f"""
            <h2>Incidencias <span class="count">{len(issue_rows)}</span></h2>
            <table class="details">
                {''.join(issue_rows)}
            </table>
        """

    return f"""\
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    body {{
        margin: 0;
        padding: 24px 12px;
        background: #101510;
        color: #e8eee8;
        font-family: Arial, Helvetica, sans-serif;
    }}
    .container {{
        max-width: 680px;
        margin: 0 auto;
        background: #182019;
        border: 1px solid #344334;
        border-radius: 12px;
        overflow: hidden;
    }}
    .header {{
        padding: 28px;
        background: #202d20;
        border-bottom: 3px solid #8aaa36;
    }}
    .brand {{
        color: #a9c85b;
        font-size: 12px;
        font-weight: bold;
        letter-spacing: 2px;
    }}
    h1 {{
        margin: 12px 0 4px;
        font-size: 26px;
    }}
    .date {{
        color: #b5c0b5;
        font-size: 14px;
    }}
    .content {{
        padding: 24px;
    }}
    .stats {{
        width: 100%;
        border-collapse: separate;
        border-spacing: 8px;
        margin: 0 -8px 24px;
    }}
    .stats td {{
        width: 33.33%;
        padding: 16px 8px;
        text-align: center;
        background: #222d22;
        border: 1px solid #354535;
        border-radius: 8px;
    }}
    .number {{
        display: block;
        color: #b4d45d;
        font-size: 28px;
        font-weight: bold;
    }}
    .stat-label {{
        color: #b5c0b5;
        font-size: 12px;
    }}
    h2 {{
        margin: 28px 0 12px;
        padding-bottom: 9px;
        border-bottom: 1px solid #394739;
        font-size: 18px;
    }}
    .count {{
        color: #b4d45d;
        font-size: 14px;
    }}
    .details {{
        width: 100%;
        border-collapse: collapse;
    }}
    .details td {{
        padding: 15px 12px;
        background: #202920;
        border-bottom: 1px solid #394739;
        overflow-wrap: anywhere;
    }}
    .post-text {{
        color: #e1e8e1;
        line-height: 1.5;
        white-space: pre-wrap;
    }}
    a {{
        color: #b4d45d;
        text-decoration: none;
    }}
    .links {{
        font-size: 13px;
    }}
    .muted {{
        color: #9eaa9e;
    }}
    .small {{
        font-size: 11px;
    }}
    .status {{
        float: right;
        color: #e6b76a;
        font-size: 11px;
    }}
    .footer {{
        padding: 18px 24px;
        color: #829082;
        font-size: 11px;
        border-top: 1px solid #344334;
    }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="brand">CALAVERA GAMING · X AGENT</div>
        <h1>Reporte diario</h1>
        <div class="date">{report_date}</div>
    </div>
    <div class="content">
        <table class="stats">
            <tr>
                <td>
                    <span class="number">{totals["like"]}</span>
                    <span class="stat-label">LIKES</span>
                </td>
                <td>
                    <span class="number">{totals["reply"]}</span>
                    <span class="stat-label">REPLIES</span>
                </td>
                <td>
                    <span class="number">{totals["repost"]}</span>
                    <span class="stat-label">REPOSTS</span>
                </td>
            </tr>
        </table>
        <p><strong>{report["total_interactions"]}</strong> interacciones confirmadas en total.</p>
        {sections}
        {issues_section}
    </div>
    <div class="footer">
        Reporte automático · Zona horaria: {html.escape(report["timezone"])}
        <br>
        Solo se contabilizan acciones confirmadas por el agente.
    </div>
</div>
</body>
</html>
"""
