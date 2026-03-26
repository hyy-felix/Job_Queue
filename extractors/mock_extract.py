"""
Mock extraction script for testing when LLM credentials are unavailable.
Produces realistic-looking extraction output without calling any API.
"""

import json
import sys
from datetime import datetime, timezone


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <url> <output_path>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    output_path = sys.argv[2]

    # Generate varied mock data based on URL hash
    import hashlib
    url_hash = int(hashlib.md5(url.encode()).hexdigest()[:8], 16)

    roles = [
        ("Software Engineer", "Stripe", "$180,000 - $250,000", "San Francisco, CA"),
        ("Mechanical Engineer", "SpaceX", "$130,000 - $170,000", "Hawthorne, CA"),
        ("Robotics Engineer", "Boston Dynamics", "$150,000 - $200,000", "Waltham, MA"),
        ("AI Engineer", "Anthropic", "$200,000 - $300,000", "San Francisco, CA (Remote)"),
        ("Data Scientist", "Netflix", "$160,000 - $220,000", "Los Gatos, CA"),
    ]
    role_title, company, salary, location = roles[url_hash % len(roles)]

    extracted = {
        "source_url": url,
        "role_title": role_title,
        "company_name": company,
        "salary": salary,
        "location": location,
        "job_description": (
            f"We are seeking a {role_title} to join {company}. "
            f"This role is based in {location}.\n\n"
            "Responsibilities:\n"
            "- Design and implement scalable systems\n"
            "- Collaborate with cross-functional teams\n"
            "- Mentor junior engineers\n"
            "- Drive technical decisions and architecture\n\n"
            "Requirements:\n"
            "- 3+ years of relevant experience\n"
            "- Strong problem-solving skills\n"
            "- BS/MS in relevant field\n"
            "- Excellent communication skills"
        ),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(extracted, f, indent=2, ensure_ascii=False)

    print(f"[MOCK] Extraction complete: {output_path}")


if __name__ == "__main__":
    main()
