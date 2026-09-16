import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import current_app

logger = logging.getLogger(__name__)


class EmailService:
    """
    Handles email notifications for pipeline events such as completion and evidence delivery.
    Supports SMTP delivery with graceful fallback to AuditLog logging when SMTP is not configured
    or encounters connection/delivery errors.
    """

    @classmethod
    def get_smtp_config(cls):
        """Extract SMTP settings from Flask config or environment variables."""
        if current_app:
            cfg = current_app.config
        else:
            from app.config.settings import Config
            cfg = Config.__dict__

        host = cfg.get("SMTP_HOST") or os.getenv("SMTP_HOST") or cfg.get("SMTP_SERVER") or os.getenv("SMTP_SERVER", "")
        port = int(cfg.get("SMTP_PORT") or os.getenv("SMTP_PORT", 587))
        user = cfg.get("SMTP_USER") or os.getenv("SMTP_USER") or cfg.get("SMTP_EMAIL") or os.getenv("SMTP_EMAIL", "")
        password = cfg.get("SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD", "")
        from_email = cfg.get("SMTP_FROM_EMAIL") or os.getenv("SMTP_FROM_EMAIL") or user or "noreply@devaa.ai"
        use_tls = str(cfg.get("SMTP_USE_TLS", os.getenv("SMTP_USE_TLS", "true"))).lower() == "true"

        return {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "from_email": from_email,
            "use_tls": use_tls,
            "configured": bool(host and user and password),
        }

    @classmethod
    def resolve_recipients(cls, story, assignee_email=None, reporter_email=None):
        """
        Resolves the email addresses for story assignee and reporter.
        """
        recipients = set()

        if assignee_email:
            recipients.add(assignee_email.strip())
        if reporter_email:
            recipients.add(reporter_email.strip())

        # If not supplied explicitly, inspect the story object
        if story:
            # Assignee relationship
            if hasattr(story, "assignee") and story.assignee and getattr(story.assignee, "email", None):
                recipients.add(story.assignee.email.strip())
            # Owner/Reporter relationship
            if hasattr(story, "owner") and story.owner and getattr(story.owner, "email", None):
                recipients.add(story.owner.email.strip())

            # Check dict structure if story is passed as a dict
            if isinstance(story, dict):
                if story.get("assignee_email"):
                    recipients.add(story["assignee_email"].strip())
                if story.get("reporter_email"):
                    recipients.add(story["reporter_email"].strip())
                if story.get("owner_email"):
                    recipients.add(story["owner_email"].strip())

        return [r for r in recipients if r and "@" in r]

    @classmethod
    def build_email_html(cls, story_info, pr_data, evidence_md):
        """
        Build an HTML email template detailing the pipeline outcome, PR links,
        acceptance criteria, and evidence summary.
        """
        story_key = story_info.get("jira_story_key") or story_info.get("external_task_id") or f"STORY-{story_info.get('id', 'N/A')}"
        title = story_info.get("title", "DEVAA Development Task")
        description = story_info.get("description", "No description provided.")
        ac = story_info.get("acceptance_criteria", "No specific acceptance criteria provided.")

        pr_url = pr_data.get("pr_url", "#")
        pr_number = pr_data.get("pr_number", "")
        branch_name = pr_data.get("branch_name", "feature-branch")
        changed_files = pr_data.get("changed_files") or []

        # Format changed files list
        files_html = "".join([f"<li style='margin-bottom: 4px; color: #1e293b;'><code>{f}</code></li>" for f in changed_files[:15]])
        if len(changed_files) > 15:
            files_html += f"<li style='color: #64748b;'><em>...and {len(changed_files) - 15} more files</em></li>"
        if not files_html:
            files_html = "<li style='color: #64748b;'>No changed files specified</li>"

        # Format AC lines as checklist items
        ac_lines = [line.strip() for line in ac.split("\n") if line.strip()]
        ac_html = "".join([f"<li style='margin-bottom: 6px; color: #334155;'>☑️ {line}</li>" for line in ac_lines[:8]])
        if not ac_html:
            ac_html = "<li style='color: #64748b;'>No criteria specified</li>"

        # Truncate evidence for email preview
        evidence_preview = (evidence_md or "").strip()
        if len(evidence_preview) > 3000:
            evidence_preview = evidence_preview[:3000] + "\n\n... [Evidence truncated for email. View full report on DEVAA PO Dashboard]"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>DEVAA: Pipeline Complete for {story_key}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #0f172a;">
  <div style="max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    
    <!-- Header -->
    <div style="background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); padding: 28px 32px; color: #ffffff;">
      <div style="display: flex; align-items: center; justify-content: space-between;">
        <span style="font-size: 11px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; background: #4338ca; padding: 4px 10px; border-radius: 6px; color: #c7d2fe;">DEVAA Autonomous Dev</span>
        <span style="font-size: 12px; background: #16a34a; padding: 4px 12px; border-radius: 9999px; font-weight: 600; color: #ffffff;">PR Created & Ready for QA</span>
      </div>
      <h1 style="margin: 16px 0 6px 0; font-size: 22px; font-weight: 700; color: #ffffff;">{story_key}: {title}</h1>
      <p style="margin: 0; font-size: 13px; color: #a5b4fc;">Autonomous multi-agent development loop completed successfully.</p>
    </div>

    <!-- Main Content -->
    <div style="padding: 32px;">

      <!-- PR Alert Banner -->
      <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <div style="font-size: 12px; font-weight: 600; color: #166534; text-transform: uppercase;">GitHub Pull Request Raised</div>
            <div style="font-size: 15px; font-weight: 600; color: #14532d; margin-top: 4px;">Branch: <code>{branch_name}</code></div>
          </div>
          {f'<a href="{pr_url}" style="display: inline-block; background: #16a34a; color: #ffffff; text-decoration: none; font-weight: 600; font-size: 13px; padding: 8px 18px; border-radius: 6px;">View PR #{pr_number} &rarr;</a>' if pr_url and pr_url != '#' else ''}
        </div>
      </div>

      <!-- Acceptance Criteria -->
      <div style="margin-bottom: 24px;">
        <h3 style="font-size: 14px; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin: 0 0 10px 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px;">Acceptance Criteria Verification</h3>
        <ul style="padding-left: 20px; margin: 0; font-size: 14px; line-height: 1.6;">
          {ac_html}
        </ul>
      </div>

      <!-- Changed Files -->
      <div style="margin-bottom: 24px;">
        <h3 style="font-size: 14px; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin: 0 0 10px 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px;">Changed Files ({len(changed_files)})</h3>
        <ul style="padding-left: 20px; margin: 0; font-size: 13px; font-family: monospace; line-height: 1.6;">
          {files_html}
        </ul>
      </div>

      <!-- Evidence Report Preview -->
      <div style="margin-bottom: 24px;">
        <h3 style="font-size: 14px; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin: 0 0 10px 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px;">Evidence Report Summary</h3>
        <div style="background: #0f172a; color: #e2e8f0; padding: 18px; border-radius: 8px; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace; font-size: 12px; line-height: 1.5; overflow-x: auto; white-space: pre-wrap; max-height: 350px;">
{evidence_preview}
        </div>
        <p style="font-size: 12px; color: #64748b; margin-top: 8px;">
          Note: In accordance with DEVAA policy, the full evidence artifact is safely stored in the DEVAA platform and PO Dashboard for download. It is never attached to Jira tickets.
        </p>
      </div>

    </div>

    <!-- Footer -->
    <div style="background: #f1f5f9; padding: 20px 32px; border-top: 1px solid #e2e8f0; text-align: center; font-size: 12px; color: #64748b;">
      <p style="margin: 0 0 4px 0;">This is an automated notification from DEVAA (Autonomous Multi-Agent Engineering Platform).</p>
      <p style="margin: 0;">Jira: {story_key} &bull; Pipeline Run Complete</p>
    </div>

  </div>
</body>
</html>
"""
        return html

    @classmethod
    def send_pipeline_completion_email(cls, story, pr_data, evidence_md, assignee_email=None, reporter_email=None, workflow_id=None):
        """
        Sends the pipeline completion email to assignee and reporter.
        If SMTP credentials are not configured or connection fails, logs to AuditLog
        without raising an exception.
        """
        story_info = story.to_dict() if hasattr(story, "to_dict") else (story if isinstance(story, dict) else {})
        story_id = story_info.get("id") or (getattr(story, "id", None) if story else None)
        story_key = story_info.get("jira_story_key") or story_info.get("external_task_id") or f"STORY-{story_id or 'N/A'}"
        title = story_info.get("title", "")

        recipients = cls.resolve_recipients(story, assignee_email, reporter_email)
        subject = f"[DEVAA] Pipeline Complete: {story_key} - {title[:60]}"
        html_content = cls.build_email_html(story_info, pr_data, evidence_md)

        cfg = cls.get_smtp_config()

        status_result = {
            "attempted_recipients": recipients,
            "subject": subject,
            "smtp_configured": cfg["configured"],
            "sent": False,
            "error": None,
        }

        # If no recipients resolved or SMTP not configured, record audit log and exit gracefully
        if not recipients:
            logger.info(f"[EmailService] No recipient email resolved for story {story_key}. Skipping SMTP dispatch.")
            status_result["error"] = "No recipient email found for assignee or reporter"
            cls._log_audit(workflow_id, story_id, "pipeline_completion_email_skipped", status_result)
            return status_result

        if not cfg["configured"]:
            logger.info(f"[EmailService] SMTP not configured (host/user/password empty). Logging email to AuditLog.")
            status_result["simulated"] = True
            status_result["error"] = "SMTP credentials not configured — payload logged to audit"
            cls._log_audit(workflow_id, story_id, "pipeline_completion_email_simulated", {
                **status_result,
                "preview_recipients": recipients,
                "subject": subject,
                "pr_url": pr_data.get("pr_url"),
            })
            return status_result

        # Attempt SMTP delivery
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = cfg["from_email"]
            msg["To"] = ", ".join(recipients)

            # Plaintext fallback
            plain_text = f"DEVAA Pipeline Complete for {story_key}: {title}\nPR: {pr_data.get('pr_url')}\nBranch: {pr_data.get('branch_name')}\n\nPlease check DEVAA Dashboard or GitHub for full evidence."
            msg.attach(MIMEText(plain_text, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            if cfg["use_tls"]:
                server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)

            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])

            server.sendmail(cfg["from_email"], recipients, msg.as_string())
            server.quit()

            logger.info(f"[EmailService] Successfully sent completion email to {recipients} for {story_key}")
            status_result["sent"] = True
            cls._log_audit(workflow_id, story_id, "pipeline_completion_email_sent", status_result)
            return status_result

        except Exception as e:
            logger.warning(f"[EmailService] Failed to send completion email via SMTP: {e}")
            status_result["error"] = str(e)
            cls._log_audit(workflow_id, story_id, "pipeline_completion_email_failed", status_result)
            return status_result

    @staticmethod
    def _log_audit(workflow_id, story_id, event_type, event_data):
        """Helper to write to DB AuditLog without raising on failure."""
        try:
            from app import db
            from app.models.devaa_models import AuditLog
            audit = AuditLog(
                workflow_id=workflow_id,
                story_id=story_id,
                event_type=event_type,
                event_data=event_data,
            )
            db.session.add(audit)
            db.session.commit()
        except Exception as ex:
            logger.warning(f"[EmailService] Failed to persist audit log: {ex}")
