
import html
import json

from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


ACTIONS = ("like", "reply", "repost")

ACTION_LABELS = {
    "like": "Likes",
    "reply": "Replies",
    "repost": "Reposts",
}


def parse_timestamp(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed
    except (ValueError, TypeError):
        return None


def get_post_url(post):
    url = post.get("url")

    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return url

    post_id = post.get("id")

    if post_id:
        return f"https://x.com/i/status/{post_id}"

    return None


def get_author(post):
    """
    Extract the X username from the original post URL.
    The tracker stores author_id but not the username.
    """
    url = get_post_url(post)

    if url:
        try:
            parts = [
                part
                for part in urlparse(url).path.split("/")
                if part
            ]

            if len(parts) >= 3 and parts[1] == "status":
                username = parts[0]

                if username.lower() not in ("i", "intent"):
                    return username
        except (ValueError, TypeError):
            pass

    author_id = post.get("author_id")

    if author_id:
        return f"ID {author_id}"

    return "Autor desconocido"


def get_action_url(action_name, action_data, post):
    external_id = action_data.get("external_id")

    if action_name == "reply" and external_id:
        external_id = str(external_id)

        if external_id.startswith(("https://", "http://")):
            return external_id

        return f"https://x.com/i/status/{external_id}"

    return get_post_url(post)


def build_daily_report(
    tracker_path="data/processed_posts.json",
    report_date=None,
    timezone_name="America/Argentina/Buenos_Aires",
):
    tracker_path = Path(tracker_path)
    local_timezone = ZoneInfo(timezone_name)

    if report_date is None:
        report_date = datetime.now(local_timezone).date()
    elif isinstance(report_date, str):
        report_date = date.fromisoformat(report_date)

    if not tracker_path.exists():
        raise FileNotFoundError(
            f"Tracker file not found: {tracker_path}"
        )

    with tracker_path.open("r", encoding="utf-8") as file:
        tracker = json.load(file)

    posts = tracker.get("posts", {})

    report = {
        "date": report_date.isoformat(),
        "timezone": timezone_name,
        "totals": {
            "like": 0,
            "reply": 0,
            "repost": 0,
            "analyzed": 0,
            "failed": 0,
            "drafted": 0,
        },
        "actions": {
            "like": [],
            "reply": [],
            "repost": [],
        },
    }

    for post_id, post in posts.items():
        if not isinstance(post, dict):
            continue

        analysis = post.get("analysis", {})
        analysis_timestamp = parse_timestamp(
            analysis.get("completed_at")
        )

        if (
            analysis.get("status") == "completed"
            and analysis_timestamp
            and analysis_timestamp.astimezone(local_timezone).date()
            == report_date
        ):
            report["totals"]["analyzed"] += 1

        for action_name in ACTIONS:
            action_data = post.get("actions", {}).get(
                action_name, {}
            )

            status = action_data.get("status")
            completed_at = parse_timestamp(
                action_data.get("completed_at")
            )
            attempted_at = parse_timestamp(
                action_data.get("last_attempt_at")
            )

            if (
                status == "completed"
                and completed_at
                and completed_at.astimezone(local_timezone).date()
                == report_date
            ):
                post_url = get_post_url(post)
                action_url = get_action_url(
                    action_name,
                    action_data,
                    post,
                )

                payload = action_data.get("payload")

                if not isinstance(payload, dict):
                    payload = {}

                item = {
                    "post_id": str(post.get("id") or post_id),
                    "author": get_author(post),
                    "text": post.get("text") or "",
                    "post_url": post_url,
                    "action_url": action_url,
                    "completed_at": completed_at.astimezone(
                        local_timezone
                    ).strftime("%H:%M:%S"),
                    "reply_text": (
                        payload.get("reply_text")
                        if action_name == "reply"
                        else None
                    ),
                }

                report["actions"][action_name].append(item)
                report["totals"][action_name] += 1

            elif (
                status == "failed"
                and attempted_at
                and attempted_at.astimezone(local_timezone).date()
                == report_date
            ):
                report["totals"]["failed"] += 1

            payload = action_data.get("payload")

            if not isinstance(payload, dict):
                payload = {}

            draft_timestamp = parse_timestamp(
                payload.get("generated_at")
            )

            if (
                status == "drafted"
                and draft_timestamp
                and draft_timestamp.astimezone(local_timezone).date()
                == report_date
            ):
                report["totals"]["drafted"] += 1

    report["totals"]["interactions"] = sum(
        report["totals"][action]
        for action in ACTIONS
    )

    return report


def build_metric_card(label, value):
    return f"""
    <td width="25%" style="padding:6px;vertical-align:top;">
        <div style="
            background:#17221c;
            border:1px solid #304638;
            border-radius:10px;
            padding:16px;
            min-height:65px;
        ">
            <div style="
                color:#a8b5aa;
                font-size:12px;
                line-height:17px;
            ">
                {html.escape(label)}
            </div>
            <div style="
                color:#b6f36b;
                font-size:26px;
                font-weight:bold;
                margin-top:6px;
            ">
                {value}
            </div>
        </div>
    </td>
    """


def build_metrics_html(totals):
    cards = [
        ("Likes", totals["like"]),
        ("Replies", totals["reply"]),
        ("Reposts", totals["repost"]),
        ("Interacciones", totals["interactions"]),
        ("Posts analizados", totals["analyzed"]),
        ("Fallos", totals["failed"]),
        ("Borradores", totals["drafted"]),
    ]

    rows = []

    for index in range(0, len(cards), 4):
        row_cards = cards[index:index + 4]

        cells = "".join(
            build_metric_card(label, value)
            for label, value in row_cards
        )

        missing_cells = 4 - len(row_cards)

        if missing_cells:
            cells += (
                '<td width="25%" style="padding:6px;"></td>'
                * missing_cells
            )

        rows.append(f"<tr>{cells}</tr>")

    return f"""
    <table
        role="presentation"
        width="100%"
        cellpadding="0"
        cellspacing="0"
        border="0"
        style="border-collapse:collapse;margin-top:14px;"
    >
        {''.join(rows)}
    </table>
    """


def build_html_report(report):
    totals = report["totals"]
    report_date = html.escape(report["date"])

    sections_html = ""

    for action_name in ACTIONS:
        items = report["actions"][action_name]
        label = ACTION_LABELS[action_name]

        sections_html += f"""
        <h2 style="
            color:#b6f36b;
            font-size:18px;
            margin-top:30px;
        ">
            {label} ({len(items)})
        </h2>
        """

        if not items:
            sections_html += """
            <p style="color:#89968c;">
                No se registraron acciones completadas.
            </p>
            """
            continue

        for item in items:
            author = html.escape(str(item["author"]))
            text = html.escape(str(item["text"]))
            time = html.escape(str(item["completed_at"]))

            post_url = item.get("post_url")
            action_url = item.get("action_url")

            post_link = ""
            action_link = ""

            if action_name == "reply":
                if action_url and action_url != post_url:
                    action_link = (
                        f'<a href="{html.escape(action_url, quote=True)}" '
                        'style="color:#b6f36b;">'
                        'Ver respuesta publicada</a>'
                    )

                if post_url:
                    post_link = (
                        f'<a href="{html.escape(post_url, quote=True)}" '
                        'style="color:#b6f36b;">'
                        'Publicación original</a>'
                    )

            else:
                # Likes and reposts only need one link:
                # both actions refer to the original post.
                if post_url:
                    action_link = (
                        f'<a href="{html.escape(post_url, quote=True)}" '
                        'style="color:#b6f36b;">'
                        'Ver publicación</a>'
                    )

            reply_text = ""

            if item.get("reply_text"):
                reply_text = f"""
                <div style="
                    background:#101914;
                    border-left:3px solid #b6f36b;
                    padding:12px;
                    margin-top:12px;
                    color:#d6dfd8;
                ">
                    {html.escape(str(item["reply_text"]))}
                </div>
                """

            links = action_link

            if post_link:
                if links:
                    links += "&nbsp; | &nbsp;"

                links += post_link

            sections_html += f"""
            <div style="
                background:#17221c;
                border:1px solid #304638;
                border-radius:10px;
                padding:16px;
                margin-bottom:12px;
            ">
                <div style="color:#b6f36b;font-weight:bold;">
                    @{author}
                </div>

                <div style="
                    color:#d6dfd8;
                    margin-top:10px;
                    white-space:pre-wrap;
                ">
                    {text}
                </div>

                {reply_text}

                <div style="
                    color:#89968c;
                    font-size:12px;
                    margin-top:12px;
                ">
                    Hora Argentina: {time}
                </div>

                <div style="margin-top:12px;">
                    {links}
                </div>
            </div>
            """

    metrics_html = build_metrics_html(totals)

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="
        margin:0;
        padding:24px;
        background:#0d1510;
        font-family:Arial,sans-serif;
        color:#e5eee7;
    ">
        <div style="max-width:850px;margin:auto;">

            <div style="
                background:#17221c;
                border:1px solid #304638;
                border-radius:12px;
                padding:24px;
            ">
                <div style="
                    color:#b6f36b;
                    font-size:13px;
                    font-weight:bold;
                    letter-spacing:2px;
                ">
                    CALAVERA GAMING
                </div>

                <h1 style="margin:12px 0 4px;font-size:26px;">
                    X Agent — Reporte diario
                </h1>

                <div style="color:#a8b5aa;">
                    {report_date} · Hora de Argentina
                </div>
            </div>

            {metrics_html}

            {sections_html}

            <div style="
                color:#657268;
                font-size:11px;
                text-align:center;
                margin-top:30px;
            ">
                Reporte automático generado por Calavera X Agent
            </div>

        </div>
    </body>
    </html>
    """
