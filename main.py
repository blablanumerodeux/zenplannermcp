#!/usr/bin/env python3
"""
Zenplanner MCP Server — FastMCP implementation.

Scrapes the Zenplanner member portal (CrossFit Pro1) to expose:
  - get_memberships()         — list available membership plans
  - get_class_schedule()      — upcoming group class calendar
  - register_for_membership() — navigate to membership signup

Run:
    cp .env.example .env   # fill in ZENPLANNER_EMAIL and ZENPLANNER_PASSWORD
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

mcp = FastMCP(
    "Zenplanner MCP",
    dependencies=[
        "httpx>=0.27.0",
        "python-dotenv>=1.0.0",
    ],
)


@mcp.tool()
def get_memberships() -> str:
    """
    List all available membership plans and prices from Zenplanner.

    Returns:
        JSON object with membership plans, names, and prices.
    """
    return service.get_memberships()


@mcp.tool()
def get_class_schedule(start_date: str | None = None) -> str:
    """
    Get the upcoming group class schedule.

    Args:
        start_date: ISO date string (YYYY-MM-DD) to start the calendar view.
                    Defaults to today.

    Returns:
        JSON array of class slots with time, name, and available spots.
    """
    return service.get_class_schedule(start_date)


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
