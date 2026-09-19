"""ZenPlanner member REST API client (studio + api2) — Cloudflare-free.

Schedule, eligibility, booking, waitlist and cancellation for a member of
CrossFit Pro1 (or any ZenPlanner tenant), reverse-engineered from the member
SPA bundles and verified live on 2026-09-18.

Endpoints (all verified against the live Pro1 tenant):
  POST https://studio.zenplanner.com/auth/v1/login
       2-step flow when the account spans multiple orgs:
         step 1 -> SUCCESS_MULTIPLE_ORGS + organizationUsers[]
         step 2 (+orgId, +userId) -> SUCCESS + token + refreshToken
  POST https://api2.zenplanner.com/elements/api-v2/member/calendars/classes/page
       body {beginDate, endDate, page, pageSize} — inclusive UTC instants
  GET  .../member/calendars/classes/{classId}/reservations/eligibility?personId=
  POST .../member/calendars/classes/{classId}/reservations   {"personId": ...}
  POST .../member/calendars/classes/{classId}/waitlists      {"personId": ...}
  POST .../member/calendars/classes/{classId}/drop-ins       {"personId": ...}
  POST .../calendars/reservations                            {"personId": ...}
  DELETE .../calendars/reserve/{reservationId}

Required headers on every api2 call (the classic gotcha):
  Authorization: Bearer <jwt>      (~12 h lifetime)
  PartitionId:  <organization id>  — without it: 403 "authorities" error
  appSource:    PORTAL
  User-Agent:   browser UA         — Cloudflare 1010 without it
"""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from src.config import ZenplannerConfig

logger = logging.getLogger(__name__)

AUTH_URL = "https://studio.zenplanner.com/auth/v1/login"
API_BASE = "https://api2.zenplanner.com/elements/api-v2/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class MemberApiError(Exception):
    """Transport / auth-level failure (API-level errors come back as dicts)."""


def _jwt_exp(token: str) -> float | None:
    """Extract the exp claim (epoch seconds) from a JWT, or None."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


class MemberApi:
    """CF-free client for the ZenPlanner member API (studio + api2 hosts)."""

    def __init__(self, config: ZenplannerConfig):
        self.config = config
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)
        self._token: str = ""
        self._refresh: str = ""
        self._exp: float = 0.0

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _login(self) -> None:
        email, password = self.config.email, self.config.password
        if not email or not password:
            raise MemberApiError("Missing ZENPLANNER_EMAIL / ZENPLANNER_PASSWORD")

        body: dict[str, Any] = {"username": email, "password": password}
        r = self._client.post(
            AUTH_URL, json=body, headers={"appSource": "PORTAL", "User-Agent": _UA}
        )
        data = self._response_json(r)
        status = data.get("status") or ""

        if not str(status).startswith("SUCCESS"):
            raise MemberApiError(f"Login failed: {status or data}")

        if not data.get("token") and status == "SUCCESS_MULTIPLE_ORGS":
            org_id = self.config.org_id
            entry = next(
                (
                    ou
                    for ou in (data.get("organizationUsers") or [])
                    if (ou.get("organization") or {}).get("id") == org_id
                ),
                None,
            )
            if entry is None:
                names = [
                    (ou.get("organization") or {}).get("name")
                    for ou in (data.get("organizationUsers") or [])
                ]
                raise MemberApiError(f"Org {org_id} not found among {names}")

            body.update(
                {
                    "orgId": org_id,
                    "userId": (entry.get("user") or {}).get("id"),
                    "requiredRoles": [],
                    "temporaryAuth": False,
                    "authCheckOnly": False,
                }
            )
            r = self._client.post(
                AUTH_URL, json=body, headers={"appSource": "PORTAL", "User-Agent": _UA}
            )
            data = self._response_json(r)
            status = data.get("status") or ""
            if status != "SUCCESS" or not data.get("token"):
                raise MemberApiError(f"Login (org select) failed: {status or data}")

        self._token = data.get("token") or ""
        self._refresh = data.get("refreshToken") or ""
        self._exp = _jwt_exp(self._token) or (time.time() + 11 * 3600)
        logger.info(
            "zenplanner: authenticated (expires in %.0f min)",
            (self._exp - time.time()) / 60,
        )

    def _ensure_token(self) -> None:
        if not self._token or time.time() > self._exp - 300:
            self._login()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "PartitionId": self.config.org_id,
            "appSource": "PORTAL",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }

    # ── Transport ─────────────────────────────────────────────────────────────

    @staticmethod
    def _response_json(r: httpx.Response) -> dict[str, Any]:
        try:
            out = r.json()
            return out if isinstance(out, dict) else {"payloadArray": out}
        except Exception:
            raise MemberApiError(
                f"Non-JSON response (HTTP {r.status_code}): {r.text[:200]}"
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        retry: bool = True,
    ) -> Any:
        """Call the api2 host. Returns parsed JSON; API errors come back as dicts."""
        self._ensure_token()
        try:
            r = self._client.request(
                method, API_BASE + path, json=json_body, params=params,
                headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise MemberApiError(f"network: {e}") from e

        if r.status_code == 401 and retry:
            self._token = ""
            self._ensure_token()
            return self._request(
                method, path, json_body=json_body, params=params, retry=False
            )

        try:
            data = r.json()
        except Exception:
            return {"http_status": r.status_code, "raw": r.text[:300]}
        if isinstance(data, dict) and r.status_code >= 400 and "error" not in data:
            data.setdefault("http_status", r.status_code)
        return data

    # ── Schedule ──────────────────────────────────────────────────────────────

    def get_day_schedule(
        self, date_str: str | None = None, tz_name: str | None = None
    ) -> dict[str, Any]:
        """Group class schedule for one local day (default: today).

        date_str: 'YYYY-MM-DD', or 'today'/'tomorrow' (also FR variants).
        """
        tz = ZoneInfo(tz_name or self.config.timezone)
        ds = (date_str or "").strip().lower()
        if ds in ("", "today", "aujourd'hui", "aujourdhui"):
            d = datetime.now(tz).date()
        elif ds in ("tomorrow", "demain"):
            d = datetime.now(tz).date() + timedelta(days=1)
        else:
            try:
                d = datetime.strptime((date_str or "").strip(), "%Y-%m-%d").date()
            except ValueError:
                return {
                    "error": {
                        "type": "bad_date",
                        "message": f"Invalid date {date_str!r}; expected YYYY-MM-DD",
                    }
                }

        lo = datetime(d.year, d.month, d.day, tzinfo=tz)
        hi = lo + timedelta(days=1) - timedelta(milliseconds=1)
        b = lo.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        e = hi.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        data = self._request(
            "POST",
            "member/calendars/classes/page",
            json_body={"beginDate": b, "endDate": e, "page": 0, "pageSize": 500},
        )
        if not isinstance(data, dict) or data.get("error"):
            err = data.get("error") if isinstance(data, dict) else data
            return {"date": d.strftime("%Y-%m-%d"), "error": err}

        now = datetime.now(timezone.utc)
        classes = [
            self._class_view(r, tz, now)
            for r in (data.get("payloadArray") or [])
            if not r.get("isCanceled")
        ]
        classes.sort(key=lambda c: c.get("start_utc") or "")
        return {
            "status": "ok",
            "date": d.strftime("%Y-%m-%d"),
            "timezone": str(tz),
            "count": len(classes),
            "classes": classes,
        }

    @staticmethod
    def _class_view(r: dict[str, Any], tz: ZoneInfo, now: datetime) -> dict[str, Any]:
        inst = r.get("instructor") or {}
        typ = r.get("type") or {}
        loc = r.get("location") or {}
        sd = r.get("startDate")
        dt = datetime.fromisoformat(sd.replace("Z", "+00:00")) if sd else None
        name = " ".join(
            x for x in [inst.get("firstName"), inst.get("lastName")] if x
        ).strip()
        return {
            "id": r.get("id"),
            "name": typ.get("name"),
            "program": (typ.get("program") or {}).get("name"),
            "start_local": dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else None,
            "start_utc": sd,
            "day": dt.astimezone(tz).strftime("%a") if dt else None,
            "duration_min": r.get("duration"),
            "instructor": name or None,
            "location": loc.get("name"),
            "available_spots": r.get("availableSpots"),
            "total_spots": r.get("totalSpots"),
            "reservation_allowed": r.get("isReservationAllowed"),
            "waitlist_allowed": r.get("isWaitlistAllowed"),
            "dropin_allowed": r.get("isDropInAllowed"),
            "reservation_window_closes": r.get("reservationWindowCloses"),
            "past": bool(dt and dt < now),
        }

    # ─ Eligibility / booking ────────────────────────────────────────────────

    def check_eligibility(self, class_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"member/calendars/classes/{class_id}/reservations/eligibility",
            params={"personId": self.config.person_id},
        )

    def book_class(self, class_id: str, join_waitlist: bool = False) -> dict[str, Any]:
        """Reserve a spot. Write action — caller confirms with the user first."""
        cid = (class_id or "").strip()
        if not cid:
            return {"status": "error", "error": {"type": "bad_input", "message": "class_id required"}}

        try:
            elig = self.check_eligibility(cid)
            payload = elig.get("payload") if isinstance(elig, dict) else None
            if isinstance(payload, dict) and payload.get("isEligible") is False:
                return {
                    "status": "not_eligible",
                    "class_id": cid,
                    "reasons": payload.get("reasons") or [],
                }

            data = self._request(
                "POST",
                f"member/calendars/classes/{cid}/reservations",
                json_body={"personId": self.config.person_id},
            )
            if isinstance(data, dict) and data.get("error"):
                if join_waitlist:
                    wdata = self._request(
                        "POST",
                        f"member/calendars/classes/{cid}/waitlists",
                        json_body={"personId": self.config.person_id},
                    )
                    if isinstance(wdata, dict) and not wdata.get("error"):
                        return {
                            "status": "waitlisted",
                            "class_id": cid,
                            "reservation_status": wdata.get("reservationStatus"),
                            "item": wdata.get("itemInfo"),
                        }
                    return {
                        "status": "error",
                        "class_id": cid,
                        "error": wdata.get("error") if isinstance(wdata, dict) else wdata,
                    }
                return {"status": "error", "class_id": cid, "error": data.get("error")}

            return {
                "status": "booked",
                "class_id": cid,
                "reservation_status": data.get("reservationStatus"),
                "item": data.get("itemInfo"),
            }
        except MemberApiError as e:
            return {"status": "error", "class_id": cid, "error": {"type": "api", "message": str(e)}}

    # ─ Reservations / cancellation ───────────────────────────────────────────

    def get_my_reservations(self) -> dict[str, Any]:
        """Upcoming reservations for this member (read-only)."""
        data = self._request(
            "POST", "calendars/reservations", json_body={"personId": self.config.person_id}
        )
        if not isinstance(data, dict) or data.get("error"):
            err = data.get("error") if isinstance(data, dict) else data
            return {"status": "error", "error": err}

        keys = (
            "id", "appointmentId", "classId", "attendanceId", "waitlistId",
            "startDate", "status", "reservationStatus", "isWaitlist", "appointmentName",
        )
        resv = [
            {k: r.get(k) for k in keys if r.get(k) is not None}
            for r in (data.get("payloadArray") or [])
        ]
        return {"status": "ok", "count": len(resv), "reservations": resv}

    def cancel_booking(self, class_id: str) -> dict[str, Any]:
        """Cancel this member's reservation. Write action — confirm first.

        Accepts a class UUID or a reservation id; resolves the right id via the
        reservations list, then DELETE calendars/reserve/{id}.
        """
        ident = (class_id or "").strip()
        if not ident:
            return {"status": "error", "error": {"type": "bad_input", "message": "class_id required"}}

        try:
            resv = self.get_my_reservations()
            match = next(
                (
                    it
                    for it in resv.get("reservations", [])
                    if ident
                    in (
                        it.get("id"),
                        it.get("appointmentId"),
                        it.get("classId"),
                        it.get("attendanceId"),
                        it.get("waitlistId"),
                    )
                ),
                None,
            )
            if match is None:
                return {
                    "status": "not_found",
                    "message": "No upcoming reservation matches that class id.",
                    "reservations_seen": resv.get("count", 0),
                }

            target = match.get("id") or match.get("attendanceId") or match.get("waitlistId") or ident
            data = self._request("DELETE", f"calendars/reserve/{target}")
            ok = isinstance(data, dict) and not data.get("error")
            return {
                "status": "canceled" if ok else "error",
                "reservation_id": target,
                "reservation_status": (data or {}).get("reservationStatus") if isinstance(data, dict) else None,
                "error": (data or {}).get("error") if isinstance(data, dict) and not ok else None,
            }
        except MemberApiError as e:
            return {"status": "error", "error": {"type": "api", "message": str(e)}}