#!/usr/bin/env python3
"""
Zenplanner MCP Server — FastMCP implementation.

Scrapes the Zenplanner member portal (CrossFit Pro1) to expose:
  - get_memberships()         — list available membership plans
  - get_class_schedule()       — upcoming group class calendar (may be Cloudflare-blocked)
  - get_profile()              — member profile info (name, membership, expiry)
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
from fastmcp import FastMCP

config = ZenplannerConfig(
    base_url=os.getenv("ZENPLANNER_BASE_URL", "https://crossfitpro1.sites.zenplanner.com"),
    email=os.getenv("ZENPLANNER_EMAIL", ""),
    password=os.getenv("ZENPLANNER_PASSWORD", ""),
    person_id=os.getenv("ZENPLANNER_PERSON_ID", ""),
)
service = ZenplannerService(config)

mcp = FastMCP("Zenplanner MCP")


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
    Get the upcoming group class schedule.

    Note: The calendar page is often blocked by Cloudflare bot protection.
    If blocked, returns an error with a workaround link to StreamFit API.

    Args:
        start_date: ISO date string (YYYY-MM-DD) to start the calendar view.
                    Defaults to today.

    Returns:
        JSON array of class slots with time, name, and available spots.
    """
    return service.get_class_schedule(start_date)


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
