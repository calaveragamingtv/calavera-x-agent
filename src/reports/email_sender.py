
import os
import smtplib
import ssl

from email.message import EmailMessage


def send_report_email(subject, html_content):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_username = os.environ["SMTP_USERNAME"]
    smtp_password = os.environ["SMTP_PASSWORD"]

    sender = os.environ.get("REPORT_EMAIL_FROM", smtp_username)
    recipients = [
        address.strip()
        for address in os.environ["REPORT_EMAIL_TO"].split(",")
        if address.strip()
    ]

    if not recipients:
        raise ValueError("REPORT_EMAIL_TO has no valid recipients")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)

    message.set_content(
        "Este correo contiene el reporte diario de Calavera X Agent. "
        "Abrí el mensaje en formato HTML para visualizarlo."
    )

    message.add_alternative(html_content, subtype="html")

    context = ssl.create_default_context()

    if smtp_port == 465:
        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            context=context,
            timeout=30,
        ) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(
            smtp_host,
            smtp_port,
            timeout=30,
        ) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_username, smtp_password)
            server.send_message(message)

    return {
        "sent": True,
        "recipients": recipients,
        "subject": subject,
    }
