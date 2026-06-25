import os
from datetime import datetime
import config

def send_email(subject: str, body: str) -> bool:
    """Send email via SendGrid."""
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        api_key = os.environ.get("SENDGRID_API_KEY", "")
        sender  = os.environ.get("EMAIL_SENDER", "")
        receiver = os.environ.get("EMAIL_RECEIVER", "")

        if not api_key:
            print("[NOTIFIER] SENDGRID_API_KEY not set — skipping email")
            return False

        message = Mail(
            from_email=sender,
            to_emails=receiver,
            subject=subject,
            plain_text_content=body,
        )

        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        response = sg.send(message)
        print(f"[NOTIFIER] Email sent: {subject} (status {response.status_code})")
        return response.status_code in (200, 201, 202)

    except Exception as e:
        print(f"[NOTIFIER] Email failed: {e}")
        return False


def notify_error(context: str, error: Exception) -> None:
    """Send error alert email."""
    subject = f"[SwingBot ERROR] {context}"
    body = (
        f"SwingBot encountered an error.\n\n"
        f"Context : {context}\n"
        f"Error   : {type(error).__name__}: {error}\n"
        f"Time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
    )
    send_email(subject, body)


def notify_trade(action: str, symbol: str, qty: int, price: float,
                 stop: float, target: float, strategy: str) -> None:
    """Send trade entry/exit notification."""
    subject = f"[SwingBot] {action} — {symbol}"
    body = (
        f"Trade Alert\n"
        f"-----------\n"
        f"Action   : {action}\n"
        f"Symbol   : {symbol}\n"
        f"Qty      : {qty}\n"
        f"Price    : ₹{price:.2f}\n"
        f"Stop     : ₹{stop:.2f}\n"
        f"Target   : ₹{target:.2f}\n"
        f"Strategy : {strategy}\n"
        f"Time     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
    )
    send_email(subject, body)


def notify_sentiment_skip(score: int, details: list) -> None:
    """Send notification when market sentiment is too weak to trade."""
    subject = "[SwingBot] Trading SKIPPED — Poor Sentiment"
    lines = "\n".join(f"  {name}: {msg} (score {s:+d})" for name, (s, msg) in details)
    body = (
        f"Sentiment check failed. No trades placed today.\n\n"
        f"Total Score : {score}\n\n"
        f"Breakdown:\n{lines}\n\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
    )
    send_email(subject, body)