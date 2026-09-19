import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ZenplannerConfig:
    base_url: str = os.getenv("ZENPLANNER_BASE_URL", "https://crossfitpro1.sites.zenplanner.com")
    email: str = os.getenv("ZENPLANNER_EMAIL", "")
    password: str = os.getenv("ZENPLANNER_PASSWORD", "")
    person_id: str = os.getenv("ZENPLANNER_PERSON_ID", "")
    # Member-API settings (studio/api2): org (partition) id + display timezone.
    # Default org = CrossFit Pro1.
    org_id: str = os.getenv("ZENPLANNER_ORG_ID", "12cdc08f-eeed-4601-85a8-cf36e8486008")
    timezone: str = os.getenv("ZENPLANNER_TZ", "America/Toronto")

    # Session state (filled after login)
    cf_id: str = field(default="", repr=False)
    cf_token: str = field(default="", repr=False)
    is_logged_in: bool = False

    def is_authenticated(self) -> bool:
        return self.is_logged_in and bool(self.cf_id)
