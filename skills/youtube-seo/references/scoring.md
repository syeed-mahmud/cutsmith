# How youtube-seo scores (and what it can't know)

All scores are 0–100 **relative signals** built from public data. They are not
monthly search volume (YouTube does not publish volume; every tool that
shows one is estimating). Compare keywords against each other, not against a absolute.

## Demand
- **Autocomplete reach (65 %)** — the keyword is typed into YouTube search at shrinking
  prefixes (80 %, 62 %, 45 %, 30 % of its length). Popular searches still appear after a
  few letters; rare ones only when typed in full. Position in the suggestion list adds a
  little. Not suggested at all → 8.
- **Views of the top results (35 %)** — median views of the first 10 results on a log
  scale (1k → 0, 10M → 100). Only when search data is available.

## Competition
From the first page of results:
- **Title match (35 %)** — share of results whose title contains every keyword word (many
  exact matches = creators deliberately target it).
- **Authority (35 %)** — channel subscribers on a log scale (API mode) or share of
  verified channels (yt-dlp mode).
- **Strength (30 %)** — median views of those results (log scale).

## Keyword score (opportunity)
`0.55 × demand + 0.45 × (100 − competition)`. Verdicts:
- demand ≥ 60 and competition ≤ 55 → great
- demand ≥ 60, competition higher → popular but crowded: go long-tail
- demand ≥ 35 and competition ≤ 50 → good niche target
- demand < 35 → secondary tag, not the main keyword

## Title score
Starts at 50; length (40–60 best, > 70 penalised), exact keyword (+18 if in the first
40 %), keyword missing (−20), a number (+6), curiosity hook (+8), emotional/benefit word
(+4); penalties for > 2 ALL-CAPS words, repeated !!/??, clichés, hashtags, ending in a
question. Bands: ≥ 80 strong, 40–79 working, < 40 weak.

## Limits
- Autocomplete and results are for the `--region`/`--lang` you pass and change daily.
- yt-dlp mode can't see subscriber counts in search lists; authority uses verified badges.
- No click-through or retention data — only YouTube Studio has that. A high score means
  "well-targeted metadata", not "will go viral".
