import os
from datetime import datetime
import config


def send_email(subject: str, body: str) -> bool:
    """Send email via SendGrid."""
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        api_key  = os.environ.get("SENDGRID_API_KEY", "")
        sender   = os.environ.get("EMAIL_SENDER", "")
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
        sg       = sendgrid.SendGridAPIClient(api_key=api_key)
        response = sg.send(message)
        print(f"[NOTIFIER] Email sent: {subject} (status {response.status_code})")
        return response.status_code in (200, 201, 202)

    except Exception as e:
        print(f"[NOTIFIER] Email failed: {e}")
        return False


def notify_error(context, error=None) -> None:
    """Send error alert. Accepts string or (context, exception) call."""
    if error is None:
        subject = "[SwingBot ERROR]"
        body    = (
            f"SwingBot encountered an error.\n\n"
            f"Error : {context}\n"
            f"Time  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
        )
    else:
        subject = f"[SwingBot ERROR] {context}"
        body    = (
            f"SwingBot encountered an error.\n\n"
            f"Context : {context}\n"
            f"Error   : {type(error).__name__}: {error}\n"
            f"Time    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
        )
    send_email(subject, body)


def notify_order_placed(symbol: str, entry: float, stop: float,
                        target: float, shares: int, strategy: str) -> None:
    """Send trade entry notification."""
    risk    = (entry - stop) * shares
    reward  = (target - entry) * shares
    subject = f"[SwingBot] BUY — {symbol}"
    body    = (
        f"Order Placed — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Symbol   : {symbol}\n"
        f"Strategy : {strategy}\n"
        f"Shares   : {shares}\n"
        f"Entry    : ₹{entry:.2f}\n"
        f"Stop     : ₹{stop:.2f}\n"
        f"Target   : ₹{target:.2f}\n"
        f"Risk     : ₹{risk:,.0f}\n"
        f"Reward   : ₹{reward:,.0f}\n"
        f"R:R      : 1:{reward/abs(risk):.1f}\n"
        f"Mode     : {'PAPER' if config.PAPER_TRADE else 'LIVE'}\n"
    )
    send_email(subject, body)


def notify_exit(symbol: str, entry: float, exit_price: float,
                shares: int, reason: str) -> None:
    """Send trade exit notification."""
    pnl     = (exit_price - entry) * shares
    pnl_pct = (exit_price - entry) / entry * 100
    subject = f"[SwingBot] EXIT — {symbol} | ₹{pnl:+,.0f}"
    body    = (
        f"Position Closed — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Symbol     : {symbol}\n"
        f"Entry      : ₹{entry:.2f}\n"
        f"Exit       : ₹{exit_price:.2f}\n"
        f"Shares     : {shares}\n"
        f"P&L        : ₹{pnl:+,.0f} ({pnl_pct:+.1f}%)\n"
        f"Reason     : {reason}\n"
        f"Mode       : {'PAPER' if config.PAPER_TRADE else 'LIVE'}\n"
    )
    send_email(subject, body)


def notify_daily_summary(executed: list, all_positions: list) -> None:
    """Send morning summary — orders placed + current open positions."""
    subject = f"[SwingBot] Morning Summary — {datetime.now().strftime('%Y-%m-%d')}"

    lines = []
    lines.append(f"SwingBot Morning Summary — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Mode: {'PAPER TRADE' if config.PAPER_TRADE else 'LIVE TRADE'}")
    lines.append("")

    if executed:
        lines.append(f"Orders Placed ({len(executed)}):")
        for sig in executed:
            lines.append(
                f"  {sig['symbol']} — {int(sig.get('shares',0))} shares @ ₹{float(sig.get('entry',0)):.2f} "
                f"| Stop: ₹{float(sig.get('stop',0)):.2f} | Target: ₹{float(sig.get('target',0)):.2f}"
            )
    else:
        lines.append("Orders Placed: None (max positions reached or no new signals)")

    lines.append("")
    lines.append(f"Open Positions ({len(all_positions)}):")
    if all_positions:
        for pos in all_positions:
            lines.append(
                f"  {pos['symbol']} — {pos['shares']} shares @ ₹{pos['entry']:.2f} "
                f"| Stop: ₹{pos['stop']:.2f} | Target: ₹{pos['target']:.2f} | Since: {pos.get('entry_date','')}"
            )
    else:
        lines.append("  No open positions.")

    send_email(subject, "\n".join(lines))


def notify_sentiment_skip(score: int, details: list) -> None:
    """Send notification when market sentiment is too weak to trade."""
    subject = "[SwingBot] Trading SKIPPED — Poor Sentiment"
    lines   = "\n".join(f"  {name}: {msg} (score {s:+d})" for name, (s, msg) in details)
    body    = (
        f"Sentiment check failed. No trades placed today.\n\n"
        f"Total Score : {score}\n\n"
        f"Breakdown:\n{lines}\n\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}\n"
    )
    send_email(subject, body)


def test_email() -> None:
    """Send a test email to verify setup."""
    send_email(
        subject="[SwingBot] Test Email",
        body=f"SwingBot email is working!\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}"
    )