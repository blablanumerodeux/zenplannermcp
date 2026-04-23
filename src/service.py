import re
import json
import httpx
import logging
from typing import Any

from src.config import ZenplannerConfig

logger = logging.getLogger(__name__)


class ZenplannerService:
    """
    Zenplanner member portal client for CrossFit Pro1.

    Known endpoints:
      POST /login.cfm                     — authenticate (CFID cookie)
      GET  /sign-up-now.cfm               — membership plans (accessible)
      GET  /calendar.cfm                  — class calendar (Cloudflare-blocked)
      GET  /person-calendar.cfm           — member reservations (accessible, no classes)
      GET  /elements/api-v2/*             — REST API (404, not available)
    """

    def __init__(self, config: ZenplannerConfig):
        self.config = config
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate(self) -> dict[str, Any]:
        """Log in and capture CFID session cookie."""
        if self.config.is_authenticated():
            return {"status": "already_authenticated"}

        login_url = f"{self.config.base_url}/login.cfm"
        payload = {
            "email": self.config.email,
            "password": self.config.password,
        }

        try:
            r = self._client.post(login_url, data=payload, headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html",
                "Referer": login_url,
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            })
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return {"status": "error", "message": f"Login failed ({e.response.status_code})"}

        # Extract CFID / CFTOKEN from cookies
        self.config.cf_id = self._client.cookies.get("CFID", "")
        self.config.cf_token = self._client.cookies.get("CFTOKEN", "")

        if not self.config.cf_id:
            # Fallback: parse from response headers or HTML
            self.config.cf_id = self._extract_cfid(r.text) or ""

        if not self.config.cf_id:
            return {"status": "error", "message": "No CFID cookie found after login"}

        self.config.is_logged_in = True
        return {"status": "authenticated", "cf_id": self.config.cf_id}

    def _extract_cfid(self, text: str) -> str:
        m = re.search(r'CFID["\s:=]+([a-zA-Z0-9\-]{20,})', text)
        return m.group(1) if m else ""

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml",
            "Referer": f"{self.config.base_url}/",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }

    # ── Memberships ─────────────────────────────────────────────────────────────

    def get_memberships(self) -> str:
        """
        Return available membership plans and prices from the sign-up page.

        Returns:
            JSON: {"status": "ok", "memberships": [{"name": "...", "price": "..."}]}
        """
        result = self.authenticate()
        if result.get("status") == "error":
            return json.dumps({"error": result["message"]})

        try:
            r = self._client.get(
                f"{self.config.base_url}/sign-up-now.cfm",
                headers=self._auth_headers(),
            )
            r.raise_for_status()
        except httpx.HTTPStatusError:
            return json.dumps({"error": f"HTTP {r.status_code}"})

        return self._parse_memberships(r.text)

    def _parse_memberships(self, html: str) -> str:
        """Extract plan name + price from sign-up-now.cfm HTML.

        Strategy: strip all HTML, split on newlines, match each line against
        the pattern  "Name ($PRICE) description".  This is robust to changes
        in the table structure.
        """
        # 1. Remove raw JS and CSS blocks
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        # 2. Replace block-level tags with newlines so each "cell" is on its own line
        for tag in ['</div>', '</td>', '</li>', '<br', '<p>', '</p>']:
            text = text.replace(tag, '\n' + tag)
        # 3. Strip all remaining tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # 4. Normalise whitespace and split
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        memberships = []
        skip_words = {
            'home', 'log in', 'calendar', 'shop online', 'workouts',
            'request info', 'sign up', 'all memberships', 'crossfit pro1',
            'responsive', 'all sign up options', 'unlimited classes',
        }
        for line in lines:
            line = line.strip()
            # Pattern: "Some Name ($123.45) description"
            m = re.match(r'^(.+?)\s*\((\$[\d\.]+)\)', line)
            if not m:
                continue
            name = m.group(1).strip()
            price = m.group(2)
            # Filter out navigation / breadcrumb noise
            if name.lower() in skip_words:
                continue
            if len(name) < 3:
                continue
            memberships.append({'name': name, 'price': price})

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique = []
        for p in memberships:
            if p['name'] not in seen:
                seen.add(p['name'])
                unique.append(p)

        if unique:
            return json.dumps({'status': 'ok', 'memberships': unique}, indent=2, ensure_ascii=False)
        # Fallback: return raw HTML snippet for debugging
        return json.dumps({'status': 'ok', 'raw': html[:1000]})

    # ── Class Schedule ─────────────────────────────────────────────────────────
    # NOTE: /calendar.cfm is blocked by Cloudflare bot protection for httpx.
    # Fallback: /person-calendar.cfm shows member reservations, not the general schedule.
    # For class browsing, use the browser-based approach or the StreamFit API for CF514.

    def get_class_schedule(self, start_date: str | None = None) -> str:
        """
        Attempt to fetch the class calendar.

        Note: /calendar.cfm is Cloudflare-blocked when accessed via httpx.
        This method returns a helpful error message with workarounds.
        """
        result = self.authenticate()
        if result.get("status") == "error":
            return json.dumps({"error": result["message"]})

        url = f"{self.config.base_url}/calendar.cfm?calendarType=PERSON:{self.config.person_id}"
        if start_date:
            url += f"&startDate={start_date}"

        try:
            r = self._client.get(url, headers=self._auth_headers())
        except httpx.HTTPStatusError:
            return json.dumps({
                "error": "calendar_blocked",
                "message": "Cloudflare is blocking calendar.cfm for httpx requests.",
                "workaround": "Use browser automation or StreamFit API for CF514 schedules.",
                "cf514_calendar_endpoint": "https://api.streamfit.com/api/v1/channels/812/calendar_workouts",
            })

        if r.status_code == 403 or "cloudflare" in r.text.lower() or "just a moment" in r.text.lower():
            return json.dumps({
                "error": "calendar_blocked",
                "message": "Cloudflare is blocking calendar.cfm for httpx requests.",
                "workaround": "Use browser automation or StreamFit API for CF514 schedules.",
                "cf514_calendar_endpoint": "https://api.streamfit.com/api/v1/channels/812/calendar_workouts",
            })

        return self._parse_calendar(r.text, start_date)

    def _parse_calendar(self, html: str, start_date: str | None) -> str:
        """Parse calendar HTML for class slots."""
        classes = []
        cells = re.findall(r'<td[^>]*class="[^"]*cal[^"]*"[^>]*>(.*?)</td>', html, re.DOTALL | re.IGNORECASE)
        for cell in cells:
            time_m = re.search(r'(\d{1,2}:\d{2}\s*(?:AM|PM))', cell)
            class_m = re.search(r'class-name[^>]*>([^<]+)', cell)
            spots_m = re.search(r'(\d+)\s*(?:spot|place)', cell, re.IGNORECASE)
            if time_m:
                classes.append({
                    "time": time_m.group(1),
                    "class": class_m.group(1).strip() if class_m else "",
                    "spots_available": spots_m.group(1) if spots_m else "unknown",
                })

        return json.dumps({
            "status": "ok",
            "start_date": start_date or "today",
            "classes": classes,
        }, indent=2)

    # ── Member Profile ─────────────────────────────────────────────────────────

    def get_profile(self) -> str:
        """
        Get member profile info from Zenplanner.

        Known accessible data:
          - Auth status + CFID from login
          - Membership type + expiry from sign-up-now.cfm (if currently enrolled)

        Known blocked pages (Cloudflare):
          - /person.cfm  → 403
          - /person-details.cfm  → Cloudflare redirect
          - /person-summary.cfm  → Cloudflare redirect
        """
        result = self.authenticate()
        if result.get("status") == "error":
            return json.dumps({"error": result["message"]})

        profile = {
            "authenticated": True,
            "cf_id": self.config.cf_id,
            "person_id": self.config.person_id,
            "email": self.config.email,
            "note": "person.cfm is Cloudflare-blocked; profile data extracted from login response",
        }

        # Try to pull membership + expiry from the sign-up-now page (has billing info for active members)
        try:
            r = self._client.get(
                f"{self.config.base_url}/sign-up-now.cfm",
                headers=self._auth_headers(),
            )
            if r.status_code == 200 and "cloudflare" not in r.text.lower():
                # Look for membership period text like "3 Months Unlimited Membership"
                m = re.search(r'(\d+)\s*Months?\s*Unlimited\s*Membership', r.text)
                x = re.search(r'(\d{2}/\d{2}/\d{2})\s*-\s*(\d{2}/\d{2}/\d{2})', r.text)
                if m:
                    profile["membership"] = m.group(0)
                if x:
                    profile["start"] = x.group(1)
                    profile["expiry"] = x.group(2)
        except Exception:
            pass

        return json.dumps({"status": "ok", "profile": profile}, indent=2, ensure_ascii=False)

    def _parse_profile(self, html: str) -> str:
        """Deprecated — Cloudflare now blocks /person.cfm. Use get_profile() instead."""
        return json.dumps({
            "error": "deprecated",
            "message": "/person.cfm is blocked by Cloudflare. Use get_profile() instead.",
        })

    # ── Register for Membership ───────────────────────────────────────────────

    def register_for_membership(self, membership_template_id: str) -> str:
        """
        Navigate to membership registration page.

        Args:
            membership_template_id: Zenplanner MembershipTemplateId GUID.
        """
        result = self.authenticate()
        if result.get("status") == "error":
            return json.dumps({"error": result["message"]})

        reg_url = (
            f"{self.config.base_url}/registration.cfm"
            f"?payment=MEMBERSHIP"
            f"&MembershipTemplateId={membership_template_id}"
            f"&personId={self.config.person_id}"
        )

        try:
            r = self._client.get(reg_url, headers=self._auth_headers())
            r.raise_for_status()
        except httpx.HTTPStatusError:
            return json.dumps({"error": f"HTTP {r.status_code}"})

        if "confirmation" in r.text.lower() or "success" in r.text.lower():
            return json.dumps({"status": "success", "message": f"Registration loaded for template {membership_template_id}"})

        return json.dumps({"status": "loaded", "message": "Registration page returned", "url": reg_url})
