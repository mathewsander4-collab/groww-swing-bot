"""
Email notification system using Gmail SMTP.
Sends alerts for every bot action.
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import config


def send_email(subject: str, body: str):
    """Send an email notification."""
    try:
        msg = MIMEMultipart()
        msg["From"] = config.EMAIL_SENDER
        msg["To"] = config.EMAIL_RECEIVER
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
            server.sendmail(config.EMAIL_SENDER, config.EMAIL_RECEIVER, msg.as_string())
        print(f"Email sent: {subject}")
    except Exception as e:
        print(f"Email failed: {e}")


def notify_order_placed(symbol: str, entry: float, stop: float,
                        target: float, shares: int, strategy: str):
    mode = "PAPER TRADE" if config.PAPER_TRADE else "LIVE TRADE"
    subject = f"[{mode}] BUY {symbol} @ ₹{entry:.2f}"
    body = f"""
Swing Bot Order — {datetime.now().strftime('%Y-%m-%d %H:%M')}
Mode: {mode}

Stock:    {symbol}
Strategy: {strategy}
Action:   BUY {shares} shares @ ₹{entry:.2f}
Stop:     ₹{stop:.2f}
Target:   ₹{target:.2f}
Capital:  ₹{shares * entry:,.0f}
Max Loss: ₹{shares * (entry - stop):,.0f}
Max Gain: ₹{shares * (target - entry):,.0f}
"""
    send_email(subject, body)


def notify_exit(symbol: str, entry: float, exit_price: float,
                shares: int, reason: str):
    pnl = (exit_price - entry) * shares
    emoji = "✅ PROFIT" if pnl > 0 else "❌ LOSS"
    mode = "PAPER TRADE" if config.PAPER_TRADE else "LIVE TRADE"
    subject = f"[{mode}] {emoji} {symbol} exited @ ₹{exit_price:.2f}"
    body = f"""
Swing Bot Exit — {datetime.now().strftime('%Y-%m-%d %H:%M')}
Mode: {mode}

Stock:      {symbol}
Exit reason: {reason}
Entry:      ₹{entry:.2f}
Exit:       ₹{exit_price:.2f}
Shares:     {shares}
P&L:        ₹{pnl:,.0f}
"""
    send_email(subject, body)


def notify_daily_summary(signals: list, open_positions: list):
    mode = "PAPER TRADE" if config.PAPER_TRADE else "LIVE TRADE"
    subject = f"[{mode}] Daily Scan — {datetime.now().strftime('%Y-%m-%d')} — {len(signals)} signals"
    
    if not signals:
        body = f"Daily Scan — {datetime.now().strftime('%Y-%m-%d')}\n\nNo setups found today."
    else:
        lines = [f"Daily Scan — {datetime.now().strftime('%Y-%m-%d')}\n",
                 f"Mode: {mode}",
                 f"Signals found: {len(signals)}\n",
                 "TODAY'S SIGNALS:",
                 "-" * 60]
        for s in signals:
            lines.append(
                f"{s['symbol']:12s} | {s['strategy']:13s} | "
                f"Entry: ₹{s['entry']:.2f} | Stop: ₹{s['stop']:.2f} | "
                f"Target: ₹{s['target']:.2f} | Shares: {s.get('shares', 0)}"
            )
        if open_positions:
            lines += ["\nOPEN POSITIONS:", "-" * 60]
            for p in open_positions:
                lines.append(
                    f"{p['symbol']:12s} | Entry: ₹{p['entry']:.2f} | "
                    f"Stop: ₹{p['stop']:.2f} | Target: ₹{p['target']:.2f}"
                )
        body = "\n".join(lines)
    
    send_email(subject, body)


def notify_error(error_msg: str):
    subject = f"[BOT ERROR] {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    body = f"Swing Bot Error:\n\n{error_msg}"
    send_email(subject, body)


def test_email():
    """Test email connection — run this first to verify setup."""
    send_email(
        subject="Swing Bot — Email Test ✅",
        body="Email notifications are working correctly!\n\nYour swing trading bot is ready."
    )


if __name__ == "__main__":
    print("Sending test email...")
    test_email()
