# Picking clips that people finish, replay and share

Score each candidate 0–100 on five dimensions, then combine. Use the result as the
`judge` value in picks.json. Be strict: a 90 should be rare, 60 is "fine but forgettable".

| Dimension | Weight | The question to ask |
|---|---|---|
| Hook | 30 % | Would a stranger stop scrolling in the first 3 seconds? |
| Standalone | 25 % | Does it make complete sense with zero context? |
| Emotion / energy | 20 % | Is there conviction, humour, surprise, vulnerability, conflict? |
| Value | 15 % | Does the viewer learn, feel, or get something specific? |
| Payoff | 10 % | Does it end on a landing — answer, punchline, reveal, verdict? |

`judge = 0.30·hook + 0.25·standalone + 0.20·emotion + 0.15·value + 0.10·payoff`

## Hook (first 3 seconds)

Strong openings, roughly strongest first:
- **Contrarian / bold claim** — "Most advice about X is wrong."
- **Curiosity gap** — "Nobody tells you this about…", "Here's the part that surprised me."
- **Stakes / danger / loss** — "This almost cost me the company."
- **Specific number or result** — "We went from 0 to 40k in six weeks."
- **Direct question to the viewer** — "Would you notice if…?"
- **Mid-action / mid-story** — opens inside the moment, energy already high.

Weak openings (score ≤ 40): greetings, "so…", "yeah, I mean…", "that's a great
question", re-stating the question slowly, any setup that the next sentence repeats.
If the best line is 5–10 s into a window, **start the clip there**.

## Standalone

- Red flags: "as I said", "going back to", "the second thing" without the first,
  an opening pronoun with no referent ("he told me…"), inside jokes from earlier.
- An interviewer's short question is a great opener — it supplies the context for free.

## Emotion / energy

Look for ⚡ lines in transcript.md (louder than the speaker's norm), laughter, strong
words ("I hate", "honestly", "terrifying"), personal stories, disagreement, confessions.
Monotone recitation of facts scores low even if the facts are good.

## Value

Specific > general. A framework, a number, a named tool/person, a step-by-step, a
counter-intuitive explanation. Platitudes ("work hard", "be consistent") score low.

## Payoff

End immediately after the landing line. Cut the trailing "…so yeah", "anyway",
the interviewer's "brilliant, thank you", or the start of the next topic.

## Length guidance

- 20–40 s: best completion rate; punchy single ideas, jokes, hot takes.
- 40–60 s: stories and explanations that need setup.
- 60–90 s: only when the arc truly needs it (use `--length long`).
A shorter clip that lands beats a longer one that drifts.

## A good set of N clips

- Different topics/angles — not three versions of the same point.
- Mix of formats if the source allows: one story, one hot take, one tip, one emotional beat.
- At least ~5 s apart in the source; never overlapping.
- If the source genuinely has fewer strong moments than N, still deliver N but say
  which ones are weaker (their `judge` should show it).

## Writing the text fields

- **hook** (title card, ≤ 7 words): the reason to keep watching. Complements the audio,
  never repeats the first spoken words. Sentence case, no hashtags, ≤ 1 emoji (usually 0).
  e.g. audio "Heat is a silent killer" → hook "40°C in the UK. Thousands died."
- **title** (3–8 words): what the clip is, for filenames and the report.
- **caption** (post text): one or two lines, curiosity + context, in the speaker's
  language. **hashtags**: 3–6, specific (topic, niche, format) over generic (#fyp).
