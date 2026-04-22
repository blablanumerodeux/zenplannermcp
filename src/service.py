import re
import httpx
import logging
from typing import Any

from src.config import ZenplannerConfig

logger = logging.getLogger(__name__)


class ZenplannerService:
    """Scrapes Zenplanner member portal for class registration and membership info."""

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
            })
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return {"status": "error", "message": f"Login failed ({e.response.status_code}): {e.response.text[:200]}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

        # Extract CFID cookie
        cf_id = self._client.cookies.get("CFID") or self._extract_cfid(r.text)
        if not cf_id:
            return {"status": "error", "message": "No CFID cookie or token found after login"}

        self.config.cf_id = cf_id
        self.config.is_logged_in = True
        return {"status": "authenticated", "cf_id": cf_id}

    def _extract_cfid(self, html: str) -> str:
        """Extract CFID from HTML (hidden form fields or cookies set via JS)."""
        m = re.search(r'CFID["\s:=]+([a-zA-Z0-9\-]{20,})', html)
        if m:
            return m.group(1)
        # Fallback: extract from Set-Cookie headers on login page itself
        return ""

    def _auth_headers(self) -> dict[str, str]:
        self.authenticate()
        return {
            "Accept": "application/json, text/html",
            "Referer": f"{self.config.base_url}/",
        }

    # ── Memberships ─────────────────────────────────────────────────────────────

    def get_memberships(self) -> str:
        """Return available memberships/plans."""
        result = self.authenticate()
        if result.get("status") == "error":
            return f'{{"error": "{result["message"]}"}}'

        try:
            r = self._client.get(
                f"{self.config.base_url}/sign-up-now.cfm",
                headers=self._auth_headers(),
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return f'{{"error": "HTTP {e.response.status_code}"}}'
        except Exception as e:
            return f'{{"error": "{str(e)}"}}'

        return self._parse_memberships(r.text)

    def _parse_memberships(self, html: str) -> str:
        """Extract membership plans from sign-up page HTML."""
        import json

        plans = []
        # Pattern: plan name + price in table rows
        rows = re.findall(
            r'<row[^>]*>(.*?)</row>',
            html, re.DOTALL | re.IGNORECASE
        )
        for row in rows:
            name_m = re.search(r"<cell[^>]*>([\w\s\-\/()éèàêç' ]+)</cell>", row)
            price_m = re.search(r'\$([0-9]+\.[0-9]+)', row)
            if name_m and price_m:
                plan_name = name_m.group(1).strip()
                if any(skip in plan_name.lower() for skip in ['sign up', 'separator', '---']):
                    continue
                plans.append({
                    "name": re.sub(r'\s+', ' ', plan_name),
                    "price": f"${price_m.group(1)}"
                })

        if plans:
            return json.dumps({"status": "ok", "memberships": plans}, indent=2)
        return json.dumps({"status": "ok", "raw": html[:2000]})

    # ── Class Calendar ─────────────────────────────────────────────────────────

    def get_class_schedule(self, start_date: str | None = None) -> str:
        """
        Fetch the group-class calendar for upcoming weeks.

        Args:
            start_date: ISO date string (YYYY-MM-DD), defaults to today
        """
        result = self.authenticate()
        if result.get("status") == "error":
            return f'{{"error": "{result["message"]}"}}'

        # Zenplanner uses Dojo-based calendar; scrape the rendered page
        url = f"{self.config.base_url}/calendar.cfm?calendarType=PERSON:{self.config.person_id}"
        if start_date:
            url += f"&startDate={start_date}"

        try:
            r = self._client.get(url, headers=self._auth_headers())
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return f'{{"error": "HTTP {e.response.status_code}"}}'
        except Exception as e:
            return f'{{"error": "{str(e)}"}}'

        return self._parse_calendar(r.text, start_date)

    def _parse_calendar(self, html: str, start_date: str | None) -> str:
        """Parse calendar HTML for class slots."""
        import json

        classes = []
        # Look for time slots in calendar cells
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

    # ── Register for Class ──────────────────────────────────────────────────────

    def register_for_membership(self, membership_template_id: str) -> str:
        """
        Navigate to membership registration.

        Args:
            membership_template_id: Zenplanner MembershipTemplateId GUID
        """
        result = self.authenticate()
        if result.get("status") == "error":
            return f'{{"error": "{result["message"]}"}}'

        reg_url = (
            f"{self.config.base_url}/registration.cfm"
            f"?payment=MEMBERSHIP"
            f"&MembershipTemplateId={membership_template_id}"
            f"&personId={self.config.person_id}"
        )

        try:
            r = self._client.get(reg_url, headers=self._auth_headers())
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return f'{{"error": "HTTP {e.response.status_code}"}}'
        except Exception as e:
            return f'{{"error": "{str(e)}"}}'

        # Check for success indicators
        if "confirmation" in r.text.lower() or "success" in r.text.lower():
            return f'{{"status": "success", "message": "Registration page loaded for template {membership_template_id}"}}'

        return f'{{"status": "loaded", "message": "Registration page returned", "html_length": {len(r.text)}}}'
