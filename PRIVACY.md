# Privacy & Data Handling

Last updated: 2026-06-25

This page describes exactly what OpenBioData collects, where it goes, and how
to get it removed. It's written to match what the code actually does, not
what a generic privacy policy template says — if you want to verify any
claim below, the file and line number are next to it.

---

## What this tool processes

When you submit an accession or a paper link, the tool fetches public NCBI
records and the linked publication (or your uploaded PDF/supplementary
file), sends that text to an LLM (Claude, with Gemini as a fallback) to
extract structured metadata, and returns it to you as a table.

## What gets stored, and where

There is no traditional database. Everything below is written to a single
Google Sheet that only I (the maintainer) hold the credentials for.

| Data | Where | What's in it |
|---|---|---|
| Every action you take (search, upload, export) | `Events` sheet | timestamp, session ID, your email (if signed in), the accession text you pasted, user agent |
| Usage summary | `UserLog` sheet | same as above, condensed |
| Signup | `Users` sheet | email, name |
| Per-user usage | `UserUsage` sheet | email, running count of samples used, the full list of every accession you've ever queried |
| Per-session usage (anonymous) | `AnonUsage` sheet | session ID, running count of samples used, the full list of every accession queried in that session — no email, not mappable to you |
| Extracted results | `KnownCachedSamples` sheet | **shared across all users** — keyed only by sample ID, not by who ran it |
| Feedback you submit | `Report` sheet | your freetext feedback + email |

**The extraction cache is global, not private to you.** If you (or anyone
else) extract metadata from a sample or paper, the structured result is
cached and reused for the next person who queries the same accession —
including people you've never met. This is how the tool keeps API costs
down for everyone and lets a 30-sample limit go further. It also means: if
a paper is paywalled and someone uploads it to extract metadata, the
*extracted fields* (not the file itself) become available to other users
querying that same accession.

## Uploaded files (PDFs, supplementary tables)

Uploaded files are processed on the server and the raw extracted text is
sent to the LLM API for that one request. Only the *fields* (e.g. country,
host, disease status) are kept afterward, in the shared cache above — not
the file itself, and not the full extracted text.

⚠️ Known gap I'm actively fixing: a few upload code paths write the file to
a temporary server directory and don't currently delete it afterward. I'm
closing this so uploaded files don't outlive the request that processed
them. Until that's done, treat an upload as "processed immediately, cleanup
in progress" rather than "instantly wiped."

## Third-party processing

Paper text and uploaded file content is sent to:
- **Anthropic (Claude)** — primary extraction
- **Google (Gemini)** — fallback if Claude is unavailable
- **Google Sheets / Google Drive API** — storage for everything in the
  table above

The following are optional integrations (see `.env.example`) that send
narrower, more derived data when configured:
- **Serper** — the accession ID or search terms being looked up, to find
  candidate papers via Google search results
- **GeoNames** — extracted location strings (e.g. a city or country name
  pulled from a paper), to standardize them to a country
- **Elsevier ScienceDirect API** — the DOI of a paper, to fetch its full
  text when it's hosted on ScienceDirect

I don't control these providers' retention windows beyond their published
API terms. I do not use your data to train any model, and I don't sell or
share it with anyone outside of running the tool itself.

## What this tool does NOT do

- It does not store your uploaded file itself in the long term — only the
  extracted structured fields (shared cache, see above).
- It does not share your email with anyone outside of running the tool.
- It does not modify any original NCBI record.

## Your choices

- **Use it anonymously.** You can run up to 10 samples without signing in.
  Signing in is free, not a trial — it raises your cap to 30 samples
  because I'm running this on my own infrastructure and want to validate
  accuracy before opening it up further. Without an email, you're only
  identified by a session ID, which I can't map back to you.
- **Delete your data.** There's no self-serve delete button yet. Email
  **vyphung1901@gmail.com** with the email or session ID you used, and I
  will remove your rows from the `Users`, `UserLog`, `UserUsage`, and
  `AnonUsage` sheets. I will not delete cached extraction results tied to a
  shared accession (since those aren't yours alone and other users rely on
  them), but I will remove anything that identifies *you* as having queried
  them.
- **Only upload files you have the legal right to use.** The tool doesn't
  check this for you — if you upload a paywalled paper, you're attesting
  you have legitimate access to it (e.g. via your institution).

## Why any of this is collected at all

- Email + usage tracking exists so your sample count can persist across
  sessions instead of resetting every time you reload, and so I can adjust
  limits for specific users on request.
- The accession text you paste is logged so I can debug failed runs and
  improve extraction accuracy — it is not used for anything else.
- The shared extraction cache exists purely to cut LLM API costs so the
  tool can stay free at this scale.

## Changes to this policy

If this changes — including when the upload-cleanup fix above ships, or if
I add a self-serve delete option — I'll update the date at the top of this
page.

## Contact

Vy Phung — vyphung1901@gmail.com
