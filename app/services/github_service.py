"""
GitHubService — PR Conversation History and Comment Management
Fetches complete PR conversation histories (issue comments, PR reviews, review comments)
and exposes helpers to post/update PR comments and descriptions.
"""
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)


class GitHubService:

    def __init__(self, token: str = None, repo_url: str = None):
        """
        Initialize with an optional PAT token and repo URL.
        Falls back to TokenSecretService for repo-specific PATs when not supplied directly.
        """
        self._token = token
        self._repo_url = repo_url
        self._headers = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            self._headers["Authorization"] = f"Bearer {self._token}"

    def get_pr_conversation(self, repo_url: str, pr_number: int) -> dict:
        """
        Fetch the complete PR conversation from GitHub.
        Returns issue_comments, reviews, review_comments, and conversation_summary.
        """
        org, repo_name = self._parse_repo(repo_url)
        if not org or not repo_name:
            logger.error(f"Could not parse org/repo from URL: {repo_url}")
            return {}

        api_base = f"https://api.github.com/repos/{org}/{repo_name}"

        issue_comments = self._fetch_all(f"{api_base}/issues/{pr_number}/comments")
        reviews = self._fetch_all(f"{api_base}/pulls/{pr_number}/reviews")
        review_comments = self._fetch_all(f"{api_base}/pulls/{pr_number}/comments")

        summary = self.build_conversation_summary(pr_number, issue_comments, reviews, review_comments)

        return {
            "issue_comments": issue_comments,
            "reviews": reviews,
            "review_comments": review_comments,
            "conversation_summary": summary,
            "pr_number": pr_number,
            "repo": f"{org}/{repo_name}"
        }

    def build_conversation_summary(
        self,
        pr_number: int,
        issue_comments: list,
        reviews: list,
        review_comments: list
    ) -> str:
        """
        Generates a structured markdown summary of the complete PR conversation
        including review verdicts, inline code comments, discussion thread, and open items.
        """
        lines = [f"## PR #{pr_number} - Complete Conversation Summary\n"]

        # Review Verdicts
        if reviews:
            lines.append("### Review Verdicts\n")
            for r in reviews:
                state = r.get("state", "COMMENTED")
                user = (r.get("user") or {}).get("login", "unknown")
                submitted_at = (r.get("submitted_at") or "")[:10]
                body = (r.get("body") or "").strip()
                emoji_map = {"APPROVED": "✅", "CHANGES_REQUESTED": "❌", "COMMENTED": "💬"}
                emoji = emoji_map.get(state, "💬")
                lines.append(f"- {emoji} **{user}** (`{state}`) on {submitted_at}")
                if body:
                    lines.append(f"  > {body[:300]}")
            lines.append("")

        # Inline Code Review Comments
        if review_comments:
            lines.append("### Inline Code Comments\n")
            for c in review_comments:
                user = (c.get("user") or {}).get("login", "unknown")
                path = c.get("path", "")
                line_no = c.get("line") or c.get("original_line") or ""
                body = (c.get("body") or "").strip()
                created_at = (c.get("created_at") or "")[:10]
                lines.append(f"- **{user}** on `{path}:{line_no}` ({created_at}): {body[:300]}")
            lines.append("")

        # General Discussion Thread
        if issue_comments:
            lines.append("### Discussion Thread\n")
            for c in issue_comments:
                user = (c.get("user") or {}).get("login", "unknown")
                body = (c.get("body") or "").strip()
                created_at = (c.get("created_at") or "")[:10]
                lines.append(f"- **{user}** ({created_at}):\n  > {body[:400]}\n")
            lines.append("")

        # Actionable Items Summary
        open_items = []
        for r in reviews:
            if r.get("state") == "CHANGES_REQUESTED":
                body = (r.get("body") or "").strip()
                if body:
                    open_items.append(f"- (Review) {body[:200]}")
        for c in review_comments:
            body = (c.get("body") or "").strip()
            path = c.get("path", "")
            if body:
                open_items.append(f"- (Code comment on `{path}`) {body[:200]}")

        if open_items:
            lines.append("### Open Action Items\n")
            lines.extend(open_items)
            lines.append("")

        approved_by = [
            (r.get("user") or {}).get("login", "unknown")
            for r in reviews if r.get("state") == "APPROVED"
        ]
        if approved_by:
            lines.append(f"### Approved By: {', '.join(approved_by)}\n")

        return "\n".join(lines)

    def post_pr_comment(self, repo_url: str, pr_number: int, body: str) -> Optional[dict]:
        """Post a comment to a GitHub PR issue thread. Returns comment dict or None."""
        org, repo_name = self._parse_repo(repo_url)
        if not org or not repo_name:
            return None
        url = f"https://api.github.com/repos/{org}/{repo_name}/issues/{pr_number}/comments"
        try:
            resp = requests.post(url, json={"body": body}, headers=self._headers, timeout=15)
            if resp.status_code == 201:
                logger.info(f"Posted comment to PR #{pr_number} on {org}/{repo_name}")
                return resp.json()
            logger.error(f"Failed to post PR comment: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Exception posting PR comment: {e}")
        return None

    def update_pr_description(self, repo_url: str, pr_number: int, title: str = None, body: str = None) -> bool:
        """Update the PR title and/or body on GitHub. Returns True on success."""
        org, repo_name = self._parse_repo(repo_url)
        if not org or not repo_name:
            return False
        url = f"https://api.github.com/repos/{org}/{repo_name}/pulls/{pr_number}"
        payload = {}
        if title:
            payload["title"] = title
        if body:
            payload["body"] = body
        if not payload:
            return False
        try:
            resp = requests.patch(url, json=payload, headers=self._headers, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Updated PR #{pr_number} description on {org}/{repo_name}")
                return True
            logger.error(f"Failed to update PR description: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Exception updating PR description: {e}")
        return False

    def merge_pr(
        self,
        repo_url: str,
        pr_number: int,
        commit_title: str = None,
        commit_message: str = None,
        merge_method: str = "squash"
    ) -> dict:
        """
        Merge a pull request on GitHub via the REST API.
        PUT /repos/{owner}/{repo}/pulls/{pull_number}/merge
        merge_method can be 'merge', 'squash', or 'rebase'. Defaults to 'squash'.
        Returns dict with keys: 'merged' (bool), 'sha' (str), 'message' (str), 'error' (str, optional)
        """
        org, repo_name = self._parse_repo(repo_url)
        if not org or not repo_name:
            err = f"Could not parse org/repo from URL: {repo_url}"
            logger.error(err)
            return {"merged": False, "error": err}

        url = f"https://api.github.com/repos/{org}/{repo_name}/pulls/{pr_number}/merge"
        payload = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message

        try:
            resp = requests.put(url, json=payload, headers=self._headers, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Successfully merged GitHub PR #{pr_number} on {org}/{repo_name} (method={merge_method})")
                return {
                    "merged": True,
                    "sha": data.get("sha"),
                    "message": data.get("message", "Pull Request successfully merged")
                }
            elif resp.status_code == 405:
                # If squash wasn't allowed or failed, try standard 'merge' method
                if merge_method != "merge":
                    logger.info(f"Retrying merge with method='merge' for PR #{pr_number}...")
                    payload["merge_method"] = "merge"
                    resp_retry = requests.put(url, json=payload, headers=self._headers, timeout=20)
                    if resp_retry.status_code == 200:
                        data = resp_retry.json()
                        logger.info(f"Successfully merged GitHub PR #{pr_number} with fallback merge method")
                        return {
                            "merged": True,
                            "sha": data.get("sha"),
                            "message": data.get("message", "Pull Request successfully merged")
                        }
                err = f"PR #{pr_number} cannot be merged (405): {resp.text}"
                logger.warning(err)
                return {"merged": False, "error": "Pull request is not mergeable or already merged", "status_code": 405}
            elif resp.status_code == 409:
                err = f"PR #{pr_number} has merge conflicts (409): {resp.text}"
                logger.warning(err)
                return {"merged": False, "error": "Merge conflict. Head branch was modified or conflicts exist.", "status_code": 409}
            else:
                err = f"GitHub merge failed ({resp.status_code}): {resp.text}"
                logger.error(err)
                return {"merged": False, "error": err, "status_code": resp.status_code}
        except Exception as e:
            logger.exception(f"Exception during GitHub PR merge: {e}")
            return {"merged": False, "error": str(e)}


    def _fetch_all(self, url: str, per_page: int = 100) -> list:
        """Paginate through all results for a GitHub API endpoint."""
        results = []
        page = 1
        while True:
            try:
                resp = requests.get(
                    url,
                    headers=self._headers,
                    params={"per_page": per_page, "page": page},
                    timeout=15
                )
                if resp.status_code != 200:
                    logger.warning(f"GitHub API returned {resp.status_code} for {url}")
                    break
                data = resp.json()
                if not data:
                    break
                results.extend(data if isinstance(data, list) else [data])
                if len(data) < per_page:
                    break
                page += 1
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                break
        return results

    @staticmethod
    def _parse_repo(repo_url: str):
        """Extract (org, repo_name) from a GitHub repo URL. Returns (None, None) on failure."""
        if not repo_url or "github.com" not in repo_url:
            return None, None
        try:
            parts = repo_url.split("github.com/")[-1].replace(".git", "").strip("/").split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]
        except Exception:
            pass
        return None, None

    @classmethod
    def from_app_config(cls, repo_url: str = None):
        """Factory: creates a GitHubService using Flask app config token or TokenSecretService."""
        try:
            from flask import current_app
            token = current_app.config.get("GITHUB_TOKEN", "").strip()
            if repo_url:
                try:
                    from app.services.token_secret_service import TokenSecretService
                    repo_token = TokenSecretService.get_token_for_repo(repo_url)
                    if repo_token:
                        token = repo_token
                except Exception:
                    pass
            return cls(token=token, repo_url=repo_url)
        except Exception:
            return cls()

