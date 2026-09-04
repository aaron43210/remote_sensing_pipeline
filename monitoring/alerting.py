# =============================================================
# OWNER: AARON
# =============================================================
import smtplib
import requests
import logging
import os
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

class AlertManager:
    """
    Production alerting for critical events.
    Supports Slack, email, and PagerDuty.
    """

    def __init__(self):
        self.slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
        self.email_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.email_port = int(os.getenv("SMTP_PORT", "587"))
        self.email_user = os.getenv("SMTP_USER")
        self.email_pass = os.getenv("SMTP_PASS")
        self.alert_email = os.getenv("ALERT_EMAIL")
        self.pagerduty_key = os.getenv("PAGERDUTY_KEY")

    def send_slack(self, message, level="info"):
        """Send alert to Slack."""
        if not self.slack_webhook:
            return

        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(level, "ℹ️")
        payload = {"text": f"{emoji} *Hyperspectral Pipeline Alert*\n{message}"}

        try:
            requests.post(self.slack_webhook, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Slack alert failed: {e}")

    def send_email(self, subject, body):
        """Send alert email."""
        if not all([self.email_user, self.email_pass, self.alert_email]):
            return

        msg = MIMEText(body)
        msg["Subject"] = f"[Hyperspectral Pipeline] {subject}"
        msg["From"] = self.email_user
        msg["To"] = self.alert_email

        try:
            with smtplib.SMTP(self.email_host, self.email_port) as server:
                server.starttls()
                server.login(self.email_user, self.email_pass)
                server.sendmail(self.email_user, self.alert_email, msg.as_string())
        except Exception as e:
            logger.error(f"Email alert failed: {e}")

    def alert_drift_detected(self, drift_score, threshold):
        msg = (
            f"Model drift detected in production!\n"
            f"Drift Score: {drift_score:.4f}\n"
            f"Threshold: {threshold:.4f}\n"
            f"Action: Retraining pipeline triggered automatically."
        )
        self.send_slack(msg, level="warning")
        self.send_email("Model Drift Detected", msg)

    def alert_pipeline_failure(self, service, error):
        msg = (
            f"Pipeline service failed!\n"
            f"Service: {service}\n"
            f"Error: {error}\n"
            f"Action: Check Kafka and service logs immediately."
        )
        self.send_slack(msg, level="critical")
        self.send_email("Pipeline Failure", msg)

    def alert_low_accuracy(self, current_acc, threshold):
        msg = (
            f"Model accuracy dropped below threshold!\n"
            f"Current Accuracy: {current_acc*100:.2f}%\n"
            f"Threshold: {threshold*100:.2f}%\n"
            f"Action: Manual review required."
        )
        self.send_slack(msg, level="critical")
        self.send_email("Low Model Accuracy", msg)

    def alert_data_quality_failure(self, scene_id, issues):
        msg = (
            f"Data quality check failed!\n"
            f"Scene: {scene_id}\n"
            f"Issues: {', '.join(issues)}\n"
            f"Action: Scene rejected, check data source."
        )
        self.send_slack(msg, level="warning")
