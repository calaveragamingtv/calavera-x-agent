
import atexit
import os
from datetime import datetime, timezone
from pathlib import Path


ACTIONS = ("like", "reply", "repost")

ACTION_LABELS = {
    "like": "LIKE",
    "reply": "REPLY",
    "repost": "REPOST",
}

STATUS_LABELS = {
    "completed": "Confirmada",
    "failed": "Fallida",
    "unknown": "Resultado incierto",
    "drafted": "Borrador / Dry run",
    "processing": "En proceso",
    "not_started": "Sin ejecutar",
}


def _parse_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _markdown_escape(value):
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _get_tracker_data(tracker):
    data = getattr(tracker, "data", None)

    if isinstance(data, dict):
        return data

    tracker_path = getattr(tracker, "path", None)

    if tracker_path:
        import json

        path = Path(tracker_path)

        if path.exists():
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)

    return {"posts": {}}


def _get_post_url(post_id, post):
    url = post.get("url")

    if url:
        return str(url)

    return f"https://x.com/i/status/{post_id}"


def _get_action_url(action_name, post_id, action, post):
    if action_name == "reply":
        external_id = action.get("external_id")

        if external_id:
            return f"https://x.com/i/status/{external_id}"

    return _get_post_url(post_id, post)


def _get_author(post):
    return (
        post.get("author_username")
        or post.get("username")
        or post.get("author")
        or post.get("author_id")
        or "Autor desconocido"
    )


def _get_reply_text(action):
    payload = action.get("payload") or {}

    if not isinstance(payload, dict):
        return ""

    return (
        payload.get("reply_text")
        or payload.get("text")
        or payload.get("content")
        or ""
    )


def _is_from_current_run(action, run_started_at):
    timestamps = (
        action.get("last_attempt_at"),
        action.get("completed_at"),
    )

    payload = action.get("payload") or {}

    if isinstance(payload, dict):
        timestamps += (payload.get("generated_at"),)

    for timestamp in timestamps:
        parsed = _parse_datetime(timestamp)

        if parsed and parsed >= run_started_at:
            return True

    return False


def _build_summary(tracker, active_post_ids, run_started_at, action_settings):
    tracker_data = _get_tracker_data(tracker)
    tracked_posts = tracker_data.get("posts", {})

    if not isinstance(tracked_posts, dict):
        tracked_posts = {}

    active_ids = {str(post_id) for post_id in active_post_ids}

    results = {
        "completed": [],
        "failed": [],
        "unknown": [],
        "drafted": [],
        "processing": [],
    }

    for post_id in active_ids:
        post = tracked_posts.get(post_id)

        if not isinstance(post, dict):
            continue

        actions = post.get("actions") or {}

        for action_name in ACTIONS:
            action = actions.get(action_name) or {}

            if not isinstance(action, dict):
                continue

            status = action.get("status", "not_started")

            if status not in results:
                continue

            if not _is_from_current_run(action, run_started_at):
                continue

            results[status].append({
                "type": action_name,
                "post_id": post_id,
                "post": post,
                "action": action,
                "url": _get_action_url(
                    action_name,
                    post_id,
                    action,
                    post,
                ),
            })

    settings = action_settings or {}
    configured_actions = settings.get("actions", {})

    modes = {}

    for action_name in ACTIONS:
        action_config = configured_actions.get(action_name, {})
        enabled = action_config.get("enabled", False)

        modes[action_name] = "LIVE" if enabled else "DRY RUN"

    now = datetime.now(timezone.utc)
    date_label = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    confirmed = len(results["completed"])
    failed = len(results["failed"])
    unknown = len(results["unknown"])
    drafted = len(results["drafted"])
    processing = len(results["processing"])

    if failed or unknown:
        overall_status = "⚠️ Finalizada con incidencias"
    elif processing:
        overall_status = "⚠️ Hay acciones en proceso"
    else:
        overall_status = "✅ Ejecución finalizada"

    lines = [
        "# Calavera X Agent",
        "",
        f"**Fecha:** {date_label}",
        "",
        f"**Estado:** {overall_status}",
        "",
        "## Resumen de interacciones",
        "",
        "| Métrica | Cantidad |",
        "|---|---:|",
        f"| Likes confirmados | {sum(1 for item in results['completed'] if item['type'] == 'like')} |",
        f"| Replies confirmados | {sum(1 for item in results['completed'] if item['type'] == 'reply')} |",
        f"| Reposts confirmados | {sum(1 for item in results['completed'] if item['type'] == 'repost')} |",
        f"| Total de interacciones confirmadas | **{confirmed}** |",
        f"| Fallidas | {failed} |",
        f"| Resultado incierto | {unknown} |",
        f"| Borradores / Dry run | {drafted} |",
        f"| En proceso | {processing} |",
        "",
        "## Configuración",
        "",
        "| Acción | Modo |",
        "|---|---|",
        f"| Like | {modes['like']} |",
        f"| Reply | {modes['reply']} |",
        f"| Repost | {modes['repost']} |",
        "",
    ]

    sections = (
        ("completed", "Interacciones confirmadas"),
        ("failed", "Interacciones fallidas"),
        ("unknown", "Resultados inciertos"),
        ("drafted", "Borradores / Dry run"),
        ("processing", "Acciones en proceso"),
    )

    for status, title in sections:
        items = results[status]

        if not items:
            continue

        lines.extend([
            f"## {title}",
            "",
        ])

        for item in sorted(
            items,
            key=lambda entry: (
                entry["type"],
                entry["post_id"],
            ),
        ):
            action_name = item["type"]
            post_id = item["post_id"]
            post = item["post"]
            action = item["action"]

            author = _markdown_escape(_get_author(post))
            original_text = _markdown_escape(post.get("text", ""))
            reply_text = _markdown_escape(_get_reply_text(action))
            url = item["url"]

            lines.extend([
                f"### {ACTION_LABELS[action_name]} — @{author}",
                "",
                f"**Estado:** {STATUS_LABELS[status]}",
                "",
                f"**Publicación:** [{post_id}]({url})",
                "",
                f"> {original_text or 'Texto original no disponible'}",
                "",
            ])

            if action_name == "reply" and reply_text:
                lines.extend([
                    "**Respuesta publicada / generada:**",
                    "",
                    f"> {reply_text}",
                    "",
                ])

            error = action.get("error")

            if error and status in ("failed", "unknown"):
                lines.extend([
                    f"**Error:** {_markdown_escape(error)}",
                    "",
                ])

            lines.append("---")
            lines.append("")

    if not any(results.values()):
        lines.extend([
            "No se registraron acciones durante esta ejecución.",
            "",
        ])

    return "\n".join(lines)


def register_execution_summary(
    tracker,
    active_post_ids,
    run_started_at,
    action_settings,
):
    """
    Registra una única generación del resumen al finalizar el proceso.

    También se ejecuta cuando main() termina mediante return
    o cuando se produce una excepción.
    """

    def write_summary():
        try:
            summary = _build_summary(
                tracker=tracker,
                active_post_ids=active_post_ids,
                run_started_at=run_started_at,
                action_settings=action_settings,
            )

            summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as file:
                    file.write(summary)
                    file.write("\n")
            else:
                print("\n" + summary)

        except Exception as error:
            print(
                "[EXECUTION SUMMARY] Failed to generate summary: "
                f"{error}"
            )

    atexit.register(write_summary)
