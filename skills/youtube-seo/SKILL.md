---
name: youtube-seo
description: YouTube keyword research and metadata optimisation — free and local. Expands a topic into real YouTube autocomplete searches, scores each keyword for demand, competition and opportunity from live search results, shows competitors' tags and title patterns, scores titles 0-100 with reasons, and checks a full title/description/tags/hashtags/chapters set against YouTube best practice. Use when the user asks for YouTube SEO, keyword research, "what should I title this", tags, a description, "will this rank", or wants titles/descriptions/tags for shorts made with shorts-clipper.
---

# YouTube SEO

You write the titles, descriptions and tags; `ytseo.py` gives you evidence (what people
search, who ranks, what they tag) and scores your drafts. Iterate until the scores and
your judgement agree. Never invent search volumes — YouTube doesn't publish them, and the
tool's numbers are 0-100 *relative* signals (explain this if the user asks for "volume").

```bash
SEO="python3 <skill-dir>/scripts/ytseo.py"     # Python 3.9+ stdlib; no setup needed
```
Data sources, best first:
- `YOUTUBE_API_KEY` env var set → YouTube Data API v3 (exact views, subscribers, tags,
  `trending`). Free key from Google Cloud Console; a `keywords` run costs ~102 quota units
  per keyword (daily quota 10,000).
- otherwise yt-dlp (found in the shorts-clipper venv or on PATH) → views, verified
  channels, and tags for the top few videos.
- neither → autocomplete demand only (`competition` = null). Say so.
Only send queries the user is fine sharing with Google/YouTube (it's normal web search).

## Workflow

1. **Topic → keywords.** `$SEO keywords "<topic>" --top 15 [--region US --lang en]`
   → ranked table: `keyword_score` (opportunity), `demand`, `competition`, `verdict`, plus
   `more_suggestions`. Read `references/scoring.md` once for what the numbers mean.
   Pick ONE main keyword (good demand, beatable competition, matches the video honestly)
   and 3–8 secondary ones. Small channels: prefer specific long-tail keywords with
   competition ≲ 50 over a crowded head term.
2. **Study the winners.** `$SEO analyze "<main keyword>"` → top results (views, channel,
   age), `competitor_tags`, frequent title words, median title length, related searches.
   Use these to choose vocabulary — never copy a competitor's title.
3. **Draft & score titles.** Write 5–8 options, then
   `$SEO title "Option 1" "Option 2" ... -k "<main keyword>"` → ranked 0–100 with reasons
   (≥ 80 strong, 40–79 working, < 40 weak). Fix what the reasons flag. The title must be
   true to the video; curiosity yes, misleading no.
4. **Description + tags + hashtags.** Write them, then
   `$SEO check --title "..." --description-file desc.txt --tags "a,b,c" -k "<kw>" [--shorts]`
   → `seo_score` + a ✓/✗ checklist with fixes. Iterate to ≥ 85.
5. **Deliver** the final title (+ 2 alternates with scores), description, tags,
   hashtags, and the keyword table with one line on why the main keyword was chosen.

Optional: `$SEO suggest "<seed>" [--deep]` (raw autocomplete expansion, fast),
`$SEO trending --region US` (most-popular videos + their tags; needs the API key).

## Best practice the checker enforces

- **Title:** main keyword early (first ~40%), 40–60 characters (≤ 70 max), specific (a
  number, a concrete outcome), one honest hook; ≤ 2 ALL-CAPS words; no hashtags; avoid
  clichés ("you won't believe").
- **Description:** keyword in the first 25 words; first line (before "Show more") states the
  payoff; ≥ 200 words for long-form (15+ for Shorts); chapters if > 3 min (first 00:00, ≥ 3,
  ascending, ≥ 10 s apart); most important link near the top; 2–3 hashtags at the end
  (YouTube shows 3; > 15 means all are ignored); clear CTA.
- **Tags:** 5–15 (Shorts 3–8), ≤ 500 characters total, exact main keyword as one tag, then
  close variants and long-tails; no generic tags (shorts, viral, fyp), no duplicates. Tags
  matter less than title and description — don't over-invest.

## With shorts-clipper

After `render`, for each clip that will go to YouTube: run steps 1–4 on the clip's topic
(`--shorts`), then write the result into that pick's
`"posts": {"youtube": {"title": ..., "description": ..., "tags": [...]}}` and re-run
`clipper.py preview` so it shows up in the gallery and REPORT.md. Keep Shorts titles short
(≤ 60 chars) and put the keyword in the first words.
