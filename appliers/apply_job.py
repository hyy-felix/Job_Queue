"""
Job application form filler via browser-use.
Invoked as a standalone subprocess via:
  uv run --project /path/to/browser-use python /path/to/apply_job.py \
    <url> <resume_pdf> <cover_letter_pdf> <output_dir> <profile_json> [--submit]

IMPORT BOUNDARY: This script runs in the browser-use venv.
It must NOT import from the main project (models, config, persistence).
All inputs arrive via CLI arguments.

DEFAULT BEHAVIOR (fill-only):
  - Navigates to application page
  - Fills form fields with candidate info
  - Uploads resume PDF
  - Takes screenshot as handoff artifact
  - Does NOT click submit — user finishes manually

OPTIONAL --submit flag:
  - After filling, also clicks the submit button
  - Use with caution: irreversible, best-effort on arbitrary sites

KNOWN LIMITATIONS (v1):
  - Login-gated applications: out of scope
  - CAPTCHAs: will fail gracefully
  - Browser closes on subprocess exit (screenshot is sole handoff artifact)
  - File upload: best-effort, may not work on drag-and-drop-only sites
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


async def apply_to_job(
    url: str,
    resume_pdf: str,
    cover_letter_pdf: str,
    output_dir: str,
    profile_json: str,
    submit: bool = False,
    cdp_url: str = "",
) -> None:
    from dotenv import load_dotenv
    load_dotenv()

    from browser_use import Agent, BrowserProfile, BrowserSession

    # ── Auto-detect LLM (same pattern as extract_job.py) ──────────
    # Order: Anthropic > BrowserUse > Google > OpenAI
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
        llm = ChatGoogle(model="gemini-3-flash", temperature=0.0)
        llm_name = "Google AI gemini-3-flash"

    elif os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"):
        from browser_use.llm import ChatGoogle
        llm = ChatGoogle(model="gemini-3-flash", temperature=0.0)
        llm_name = "Vertex AI gemini-3-flash"

    elif os.environ.get("OPENAI_API_KEY"):
        from browser_use.llm import ChatOpenAI
        llm = ChatOpenAI(model="gpt-4o", temperature=0.0)
        llm_name = "OpenAI gpt-4o"

    if llm is None:
        print(
            "ERROR: No LLM API key found. Set one of: ANTHROPIC_API_KEY, "
            "BROWSER_USE_API_KEY, GOOGLE_API_KEY, GOOGLE_GENAI_USE_VERTEXAI, "
            "OPENAI_API_KEY",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using LLM: {llm_name}")

    # ── Load candidate profile ────────────────────────────────────
    profile = {}
    profile_path = Path(profile_json)
    if profile_path.exists():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        print(f"Loaded profile: {profile.get('full_name', 'unknown')}")
    else:
        print(f"WARNING: Profile not found at {profile_json}, filling job-specific fields only")

    # ── Validate resume exists ────────────────────────────────────
    resume_path = Path(resume_pdf)
    if not resume_path.exists():
        _write_result(output_dir, url, llm_name, status="failed",
                      error=f"Resume PDF not found: {resume_pdf}")
        sys.exit(1)

    cover_letter_path = Path(cover_letter_pdf)
    has_cover_letter = cover_letter_path.exists()

    # ── Build the browser-use task prompt ─────────────────────────
    profile_info = ""
    if profile:
        # Build full name from first_name + last_name, or fall back to full_name
        full_name = profile.get('full_name', '')
        if not full_name:
            first = profile.get('first_name', '')
            last = profile.get('last_name', '')
            full_name = f"{first} {last}".strip()

        # Build full address from components, or fall back to location
        address_parts = []
        if profile.get('address'):
            address_parts.append(profile['address'])
        if profile.get('city'):
            address_parts.append(profile['city'])
        if profile.get('postal_code'):
            address_parts.append(profile['postal_code'])
        if profile.get('country'):
            address_parts.append(profile['country'])
        location = ', '.join(address_parts) if address_parts else profile.get('location', '')

        profile_info = f"""
Candidate information to fill into the form (USE EXACTLY THESE VALUES, do NOT make up or guess any information):
- Full Name: {full_name}
- First Name: {profile.get('first_name', '')}
- Last Name: {profile.get('last_name', '')}
- Email: {profile.get('email', '')}
- Phone: {profile.get('phone', '')}
- Address: {profile.get('address', '')}
- City: {profile.get('city', '')}
- State/Province: {profile.get('state', '')}
- Postal Code: {profile.get('postal_code', '')}
- Country: {profile.get('country', '')}
- Full Location: {location}
- LinkedIn: {profile.get('linkedin_url', '')}
- Website: {profile.get('website', '')}
- Age: {profile.get('age', '')}
- Gender: {profile.get('gender', '')}
- Race/Ethnicity: {profile.get('race', '')}
- US Citizen: {profile.get('US_citizen', '')}
- Sponsorship Needed: {profile.get('sponsorship_needed', '')}
- Veteran Status: {profile.get('Veteran_status', '')}
- Disability Status: {profile.get('disability_status', '')}

IMPORTANT: Only use the EXACT values listed above. If a field is empty above, leave it blank on the form. NEVER guess or make up addresses, phone numbers, or any other personal information.
"""

    submit_instruction = ""
    if submit:
        submit_instruction = """
After filling all fields and uploading the resume:
- Click the submit/apply button to submit the application.
- If there is a confirmation dialog, confirm it.
"""
    else:
        submit_instruction = """
IMPORTANT: Do NOT click the submit/apply button.
Stop after filling all fields and uploading the resume.
The user will review and submit manually.
"""

    task = f"""
Go to this URL: {url}

Find the job application form on this page. If there's an "Apply" or "Apply Now"
button that leads to an application form, click it first.

{profile_info}

Fill in the application form:
1. Fill all text fields you can match to the candidate information above.
2. Upload the resume file from this path: {resume_pdf}
3. {"Upload the cover letter from: " + cover_letter_pdf if has_cover_letter else "Skip cover letter upload if not required."}
4. For any dropdown fields (e.g., country, state), select the closest match.
5. For fields you don't have data for, leave them empty rather than guessing.

After filling the form:
- Check if the resume file input shows a filename (not empty). Report whether
  the upload appears successful.
- List every field you successfully filled.
- List any fields you could not fill and why.

{submit_instruction}

Finally, take a screenshot of the current page state.

Return a JSON object with these exact keys:
- fields_filled: array of field names you successfully filled
- fields_failed: array of field names you could not fill
- resume_uploaded: boolean, true if the file input shows a filename
- error: null if no issues, or a string describing what went wrong

No markdown formatting. No explanation. Just the JSON.
"""

    # ── Run the browser-use agent ─────────────────────────────────
    print(f"Navigating to: {url}")
    print(f"Submit mode: {'ON (will click submit)' if submit else 'OFF (fill only)'}")

    result_data = {
        "status": "failed",
        "screenshot_path": None,
        "url_navigated": url,
        "fields_filled": [],
        "fields_failed": [],
        "resume_uploaded": False,
        "error": None,
        "llm_used": llm_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dom_fields": [],  # Extracted form field values for review agent
    }

    # Build browser session — connect to existing Chrome if CDP URL provided
    browser_session = None
    if cdp_url:
        print(f"Connecting to existing browser at: {cdp_url}")
        profile_obj = BrowserProfile(
            cdp_url=cdp_url,
            is_local=True,
            keep_alive=True,
        )
        browser_session = BrowserSession(browser_profile=profile_obj)

    # Collect file paths for browser-use upload (DOM.setFileInputFiles)
    upload_files = [str(resume_path)]
    if has_cover_letter:
        upload_files.append(str(cover_letter_path))

    try:
        if browser_session:
            # Open job URL in a NEW TAB (don't navigate away from existing tabs)
            agent = Agent(
                task=task,
                llm=llm,
                browser_session=browser_session,
                initial_actions=[{'navigate': {'url': url, 'new_tab': True}}],
                directly_open_url=False,  # Prevent auto-navigate which uses current tab
                available_file_paths=upload_files,
            )
        else:
            agent = Agent(task=task, llm=llm, available_file_paths=upload_files)
        result = await agent.run(max_steps=50)

        # ── Take screenshot ───────────────────────────────────────
        screenshot_path = Path(output_dir) / "apply_evidence.png"
        try:
            if hasattr(agent, 'browser') and agent.browser:
                page = await agent.browser.get_current_page()
                if page:
                    await page.screenshot(path=str(screenshot_path))
                    result_data["screenshot_path"] = "apply_evidence.png"
                    print(f"Screenshot saved: {screenshot_path}")
        except Exception as ss_err:
            print(f"WARNING: Screenshot failed: {ss_err}")

        # ── Extract DOM form fields for review agent ───────────────
        try:
            if hasattr(agent, 'browser') and agent.browser:
                page = await agent.browser.get_current_page()
                if page:
                    dom_fields = await page.evaluate("""
                        Array.from(document.querySelectorAll('input, select, textarea')).map(el => ({
                            name: el.name || el.id || el.getAttribute('aria-label') || '',
                            type: el.type || el.tagName.toLowerCase(),
                            value: el.value || '',
                            placeholder: el.placeholder || '',
                            visible: el.offsetParent !== null
                        })).filter(f => f.visible && f.name)
                    """)
                    result_data["dom_fields"] = dom_fields
                    print(f"Extracted {len(dom_fields)} DOM form fields")
        except Exception as dom_err:
            print(f"WARNING: DOM field extraction failed: {dom_err}")

        # ── Parse agent result ────────────────────────────────────
        if result and result.final_result():
            final_text = result.final_result()
            text = final_text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines).strip()

            try:
                parsed = json.loads(text)
                result_data["fields_filled"] = parsed.get("fields_filled", [])
                result_data["fields_failed"] = parsed.get("fields_failed", [])
                result_data["resume_uploaded"] = parsed.get("resume_uploaded", False)
                result_data["error"] = parsed.get("error")

                # Determine status based on resume upload verification
                if result_data["resume_uploaded"]:
                    result_data["status"] = "form_filled"
                elif result_data["fields_filled"]:
                    # Fields were filled but resume upload unconfirmed
                    result_data["status"] = "partial_fill"
                else:
                    result_data["status"] = "failed"
                    if not result_data["error"]:
                        result_data["error"] = "No fields were filled"

            except json.JSONDecodeError:
                result_data["error"] = f"Agent returned non-JSON: {final_text[:200]}"
        else:
            result_data["error"] = "Agent returned no result"

    except Exception as exc:
        result_data["error"] = f"Agent error: {exc}"
        print(f"ERROR: {exc}", file=sys.stderr)

    # ── Write result JSON ─────────────────────────────────────────
    _write_result_dict(output_dir, result_data)

    # ── Exit code ─────────────────────────────────────────────────
    if result_data["status"] in ("form_filled", "partial_fill"):
        print(f"Apply complete: status={result_data['status']}, "
              f"fields={len(result_data['fields_filled'])}, "
              f"resume_uploaded={result_data['resume_uploaded']}")
        sys.exit(0)
    else:
        print(f"Apply failed: {result_data['error']}", file=sys.stderr)
        sys.exit(1)


def _write_result(output_dir: str, url: str, llm_name: str,
                  status: str = "failed", error: str | None = None) -> None:
    """Write a minimal failure result."""
    _write_result_dict(output_dir, {
        "status": status,
        "screenshot_path": None,
        "url_navigated": url,
        "fields_filled": [],
        "fields_failed": [],
        "resume_uploaded": False,
        "error": error,
        "llm_used": llm_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def _write_result_dict(output_dir: str, data: dict) -> None:
    """Write the apply result JSON to the output directory."""
    result_path = Path(output_dir) / "apply_result.json"
    result_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Result written: {result_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Auto-fill job application forms")
    parser.add_argument("url", help="Job posting URL")
    parser.add_argument("resume_pdf", help="Path to resume PDF")
    parser.add_argument("cover_letter_pdf", help="Path to cover letter PDF (or empty string)")
    parser.add_argument("output_dir", help="Directory for output artifacts")
    parser.add_argument("profile_json", help="Path to profile.json")
    parser.add_argument("--submit", action="store_true", help="Click submit after filling (default: fill only)")
    parser.add_argument("--cdp-url", default="", help="CDP URL for existing Chrome (e.g., http://localhost:9222)")
    args = parser.parse_args()

    print(f"Apply job: {args.url}")
    print(f"Resume: {args.resume_pdf}")
    print(f"Cover letter: {args.cover_letter_pdf}")
    print(f"Output dir: {args.output_dir}")
    print(f"Profile: {args.profile_json}")
    print(f"Submit: {args.submit}")
    if args.cdp_url:
        print(f"CDP mode: connecting to {args.cdp_url}")

    asyncio.run(apply_to_job(
        args.url, args.resume_pdf, args.cover_letter_pdf,
        args.output_dir, args.profile_json, args.submit, args.cdp_url,
    ))


if __name__ == "__main__":
    main()
