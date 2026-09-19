#!/usr/bin/env python3
"""
Zenplanner MCP Server — FastMCP implementation.

Scrapes the Zenplanner member portal (CrossFit Pro1) to expose:
  - get_day_schedule()          — one-day group class schedule (member API, CF-free)
  - check_class_eligibility()   — can this member reserve a class?
  - book_class()                — reserve a spot (or waitlist)
  - get_my_reservations()       — upcoming reservations
  - cancel_booking()            — cancel a reservation
  - get_memberships()           — list available membership plans
  - get_class_schedule()        — day view (alias of get_day_schedule, kept for back-compat)
  - get_profile()               — member profile info (name, membership, expiry)
  - register_for_membership()   — navigate to membership signup

Run:
    cp .env.example .env   # fill in ZENPLANNER_EMAIL, ZENPLANNER_PASSWORD, ZENPLANNER_PERSON_ID
    pip install -r requirements.txt
    python main.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

from src.config import ZenplannerConfig
from src.service import ZenplannerService
from src.member_api import MemberApi
from fastmcp import FastMCP

config = ZenplannerConfig(
    base_url=os.getenv("ZENPLANNER_BASE_URL", "https://crossfitpro1.sites.zenplanner.com"),
    email=os.getenv("ZENPLANNER_EMAIL", ""),
    password=os.getenv("ZENPLANNER_PASSWORD", ""),
    person_id=os.getenv("ZENPLANNER_PERSON_ID", ""),
)
service = ZenplannerService(config)
member = MemberApi(config)

mcp = FastMCP("Zenplanner MCP")


def _dump(obj) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def get_day_schedule(date: str | None = None) -> str:
    """
    Get the group class schedule for ONE day at CrossFit Pro1 (Cloudflare-free
    member API). This is the primary schedule tool — much faster and more
    reliable than the Cloudflare-blocked calendar page.

    Args:
        date: 'YYYY-MM-DD', or 'today' / 'tomorrow' (FR: 'demain').
              Defaults to today.

    Returns:
        JSON: {"status": "ok", "date": "...", "count": N, "classes": [
            {"id": ..., "name": "Group Class CrossFit", "start_local": "2026-09-19 10:00",
             "duration_min": 60, "instructor": "...", "available_spots": 4,
             "total_spots": 12, "reservation_allowed": true, ...}]}
        Class "id" is what book_class()/cancel_booking() take.
    """
    return _dump(member.get_day_schedule(date))


@mcp.tool()
def check_class_eligibility(class_id: str) -> str:
    """
    Check whether the member is eligible to reserve a given class.

    Args:
        class_id: Class UUID from get_day_schedule().

    Returns:
        JSON: {"payload": {"isEligible": true, "reasons": []}} (or the reasons).
    """
    if not class_id or not class_id.strip():
        return _dump({"error": "class_id cannot be empty"})
    try:
        return _dump(member.check_eligibility(class_id.strip()))
    except Exception as e:  # noqa: BLE001
        return _dump({"error": {"type": "api", "message": str(e)}})


@mcp.tool()
def book_class(class_id: str, join_waitlist: bool = False) -> str:
    """
    Reserve the member's spot in a class (WRITE action).

    Checks eligibility first, then reserves. If the class is full and
    join_waitlist=true, falls back to the waitlist endpoint.

    Args:
        class_id: Class UUID from get_day_schedule().
        join_waitlist: If true and reservation fails (e.g. full), try waitlist.

    Returns:
        JSON: {"status": "booked"|"waitlisted"|"not_eligible"|"error", ...}
    """
    if not class_id or not class_id.strip():
        return _dump({"status": "error", "error": {"type": "bad_input", "message": "class_id required"}})
    return _dump(member.book_class(class_id.strip(), join_waitlist))


@mcp.tool()
def get_my_reservations() -> str:
    """
    List the member's upcoming class reservations.

    Returns:
        JSON: {"status": "ok", "count": N, "reservations": [
            {"id": ..., "startDate": ..., "status": ..., ...}]}
    """
    return _dump(member.get_my_reservations())


@mcp.tool()
def cancel_booking(class_id: str) -> str:
    """
    Cancel the member's reservation for a class (WRITE action).

    Args:
        class_id: Class UUID (from get_day_schedule or get_my_reservations).

    Returns:
        JSON: {"status": "canceled", "reservation_id": ..., ...}
    """
    if not class_id or not class_id.strip():
        return _dump({"status": "error", "error": {"type": "bad_input", "message": "class_id required"}})
    return _dump(member.cancel_booking(class_id.strip()))


@mcp.tool()
def get_memberships() -> str:
    """
    List all available membership plans and prices from Zenplanner.

    Returns:
        JSON: {"status": "ok", "memberships": [{"name": "...", "price": "..."}]}
    """
    return service.get_memberships()


@mcp.tool()
def get_class_schedule(start_date: str | None = None) -> str:
    """
    Get the upcoming group class schedule (day view, Cloudflare-free).

    Prefer get_day_schedule() — same data, clearer name. This tool is kept
    for backwards compatibility and now uses the member API.

    Args:
        start_date: 'YYYY-MM-DD' or 'today'/'tomorrow'. Defaults to today.

    Returns:
        JSON: same shape as get_day_schedule().
    """
    return _dump(member.get_day_schedule(start_date))


@mcp.tool()
def get_profile() -> str:
    """
    Get the member's profile info from Zenplanner.

    Returns:
        JSON: {"status": "ok", "profile": {"name": "...", "membership": "...", "expiry": "..."}}
    """
    return service.get_profile()


@mcp.tool()
def register_for_membership(membership_template_id: str) -> str:
    """
    Navigate to a membership registration page.

    Args:
        membership_template_id: The Zenplanner MembershipTemplateId GUID.
                               Use get_memberships() to find plan IDs.

    Returns:
        Confirmation or error message.
    """
    if not membership_template_id or not membership_template_id.strip():
        return '{"error": "membership_template_id cannot be empty"}'
    return service.register_for_membership(membership_template_id.strip())


if __name__ == "__main__":
    import sys

    missing = [k for k in ("ZENPLANNER_EMAIL", "ZENPLANNER_PASSWORD") if not os.getenv(k)]
    if missing:
        print(
            f"WARNING: Missing environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in your credentials.\n",
            file=sys.stderr,
        )

    mcp.run(transport="stdio")
