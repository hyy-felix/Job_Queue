"""
Job extraction script.
Invoked as a standalone subprocess via:
  uv run --project /path/to/browser-use python /path/to/extract_job.py <url> <output_path> [--cdp-url URL]

This script runs in the browser-use environment (its dependencies).
It writes structured JSON output to <output_path>.
It does NOT write to status.json — only the orchestrator does that.

CDP mode (--cdp-url):
  Connects to an already-running Chrome instance instead of launching a new one.
  This allows you to log into LinkedIn once, then extract multiple jobs without
  re-authenticating. The browser stays open between extractions.

  Launch Chrome with:
    /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
      --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-job-queue"
"""

import asyncio
import json
import sys
from datetime import datetime, timezone


async def extract(url: str, output_path: str, cdp_url: str = "") -> None:
    import os
    from dotenv import load_dotenv

    # Load .env from the browser-use repo (where this script runs via uv run --project)
    load_dotenv()

    from browser_use import Agent, BrowserProfile, BrowserSession

    # Auto-detect LLM based on available credentials (order: Anthropic > BrowserUse > Google > OpenAI)
    llm = None
    llm_name = None

    if os.environ.get("ANTHROPIC_API_KEY"):
        from browser_use.llm import ChatAnthropic
        llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0.0)
        llm_name = "Anthropic claude-sonnet-4-6"

    elif os.environ.get("BROWSER_USE_API_KEY"):
        from browser_use import ChatBrowserUse
        llm = ChatBrowserUse(model="bu-2-0")
        llm_name = "BrowserUse bu-2-0"

    elif os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
        from browser_use.llm import ChatGoogle
        llm = ChatGoogle(model="gemini-2.0-flash", temperature=0.0)
        llm_name = "Google AI gemini-2.0-flash"

    elif os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"):
        from browser_use.llm import ChatGoogle
        llm = ChatGoogle(model="gemini-2.0-flash", temperature=0.0)
        llm_name = "Vertex AI gemini-2.0-flash"

    elif os.environ.get("OPENAI_API_KEY"):
        from browser_use.llm import ChatOpenAI
        llm = ChatOpenAI(model="gpt-4o", temperature=0.0)
        llm_name = "OpenAI gpt-4o"

    if llm is None:
        print("ERROR: No LLM API key found.", file=sys.stderr)
        sys.exit(1)

    print(f"Using LLM: {llm_name}")

    # Build browser profile — connect to existing Chrome if CDP URL provided
    browser_session = None
    if cdp_url:
        print(f"Connecting to existing browser at: {cdp_url}")
        profile = BrowserProfile(
            cdp_url=cdp_url,
            is_local=True,
            keep_alive=True,  # Don't close the browser after extraction
        )
        browser_session = BrowserSession(browser_profile=profile)

    task = f"""
    Go to this URL: {url}

    IMPORTANT: You MUST read the entire page content carefully before responding.
    Do NOT return null values without first scrolling through and reading the page.

    After reading the full page, extract these fields:
    1. role_title: The exact job title shown on the page
    2. company_name: The company name
    3. salary: Salary or compensation info (null ONLY if truly not listed anywhere)
    4. location: Job location (city, state, remote, etc.)
    5. job_description: Copy the COMPLETE job description text from the page.
       Include ALL sections: overview, responsibilities, requirements,
       qualifications, benefits. This must be comprehensive — multiple paragraphs.
       Do NOT summarize. Copy the actual text from the page.

    If the page shows a 404 error or the job posting is not found, set
    job_description to a message explaining the page was not found.

    Return ONLY a valid JSON object with these exact keys:
    role_title, company_name, salary, location, job_description

    No markdown formatting. No explanation. Just the JSON.
    """

    if browser_session:
        # Open job URL in a NEW TAB (don't navigate away from existing tabs)
        agent = Agent(
            task=task,
            llm=llm,
            browser_session=browser_session,
            initial_actions=[{'navigate': {'url': url, 'new_tab': True}}],
            directly_open_url=False,  # Prevent auto-navigate which uses current tab
        )
    else:
        agent = Agent(task=task, llm=llm)

    result = await agent.run(max_steps=20)

    # Parse the agent's final result
    extracted = {
        "source_url": url,
        "role_title": None,
        "company_name": None,
        "salary": None,
        "location": None,
        "job_description": None,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    if result and result.final_result():
        final_text = result.final_result()

        # Strip markdown code fences if present
        text = final_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
            for key in ("role_title", "company_name", "salary", "location", "job_description"):
                if key in parsed:
                    extracted[key] = parsed[key]
        except json.JSONDecodeError:
            extracted["job_description"] = final_text

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(extracted, f, indent=2, ensure_ascii=False)

    print(f"Extraction complete: {output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract job data from a URL")
    parser.add_argument("url", help="Job posting URL")
    parser.add_argument("output_path", help="Path to write JSON output")
    parser.add_argument("--cdp-url", default="", help="CDP URL for existing Chrome (e.g., http://localhost:9222)")
    args = parser.parse_args()

    print(f"Extracting job data from: {args.url}")
    if args.cdp_url:
        print(f"CDP mode: connecting to {args.cdp_url}")
    asyncio.run(extract(args.url, args.output_path, args.cdp_url))


if __name__ == "__main__":
    main()
