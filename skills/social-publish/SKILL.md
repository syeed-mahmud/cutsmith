---
name: social-publish
description: Post and schedule videos (e.g. shorts-clipper clips) DIRECTLY to YouTube, Facebook Pages, Instagram, LinkedIn, TikTok, Bluesky, Threads, Reddit, Telegram, Discord, Mastodon, Pinterest (and X, which is pay-per-use) using the user's own free developer apps. Builds a publish plan from rendered clips, shows it in chat, posts only the entries the user approves, schedules (YouTube/Facebook natively, others via a local cron job), and tracks public URLs. Use when the user wants to post, publish, upload, schedule or cross-post clips to social media, connect a social account, or set up a posting calendar.
---

# Social publish (direct, free)

Posting is public and hard to undo. **Only entries the user approved in this conversation
get published.** Show the plan, let them pick, record it with `approve`, then `run --yes`.
Approval of one plan never carries over to another.

```bash
PUB="python3 <skill-dir>/scripts/publish.py"     # stdlib only, no install
```
Credentials live in `~/.shorts-clipper/social/` (apps.json + tokens.json, mode 600). Never
print, paste or commit them. If the user pastes a secret into chat, use it, and suggest
they rotate it afterwards.

## One-time: connect platforms (the user does the browser steps)

`$PUB setup <platform>` prints the exact steps for that platform. Summary:

| Platform | What the user creates (free) | Then | Notes |
|---|---|---|---|
| youtube | Google Cloud project → enable YouTube Data API v3 → OAuth client "Desktop app", add self as test user | `setup youtube --client-id … --client-secret …` → `auth youtube` | 100 uploads/day. In "Testing" mode the login expires every 7 days — "Publish app" on the consent screen avoids that. |
| facebook + instagram | Meta app (Business) → Graph API Explorer user token with pages + instagram_content_publish permissions | `setup meta --app-id … --app-secret …` → `auth meta --user-token …` | Your own Pages need no App Review. IG must be a professional account linked to the Page. FB Reels 3–90 s, 30/day; IG 100/day. |
| linkedin | LinkedIn developer app + products "Share on LinkedIn" + "Sign In with LinkedIn (OIDC)", redirect `http://127.0.0.1:8765/callback` | `setup linkedin …` → `auth linkedin` | Personal profile. Token lasts 60 days → re-run `auth`. |
| tiktok | TikTok developer app (Desktop) + Login Kit + Content Posting API, redirect `http://127.0.0.1:*/callback/` | `setup tiktok --client-key … --client-secret …` → `auth tiktok` | Default **draft** mode: video goes to the TikTok inbox, the user taps publish in the app. `direct` mode is private-only until TikTok audits the app. |
| bluesky | App password (Settings → App passwords) | `setup bluesky --handle … --app-password …` → `auth bluesky` | free |
| telegram | Bot from @BotFather, added as admin to the channel/group | `setup telegram --client-secret <bot token>` → `auth telegram` (prints a `/connect` code to send in the chat) | free; bot uploads ≤ 50 MB |
| discord | Bot app added to the server (Send Messages + Attach Files) | `setup discord --client-secret <bot token>` → `auth discord --channel <id>` | free; ≤ 25 MB on unboosted servers |
| mastodon | Access token from your instance (Development → New application) | `setup mastodon --handle https://instance --client-secret <token>` → `auth mastodon` | free |
| pinterest | Pinterest developer app, redirect `http://127.0.0.1:8768/callback` | `setup pinterest --client-id … --client-secret …` → `auth pinterest [--board id]` | free; new apps start in trial access |
| threads | Meta app with the "Access the Threads API" use case, redirect `https://localhost:8770/threads-callback`, yourself as Threads tester | `setup threads --client-id … --client-secret … [--media-host-cmd '…']` → `auth threads` (paste the https://localhost/… URL the browser lands on) | free; **needs the video at a public URL**: `options.public_url` or a `media_host_cmd` the user chose (their bucket/server). Never upload to a third-party host the user hasn't picked. Max 5 min, 250 posts/day |
| reddit | reddit.com/prefs/apps "web app", redirect `http://127.0.0.1:8769/callback` | `setup reddit --client-id … --client-secret …` → `auth reddit` | free for personal use; `plan … --subreddit name`; respect each subreddit's self-promotion rules |
| x | X developer app with OAuth 2.0 | `setup x …` → `auth x` | **Not free**: X API is pay-per-use (~$0.015/post). Only if the user accepts. |

`auth` opens a browser and waits for the login to finish. That needs the user, so tell them
to run it themselves in the Claude Code prompt with `!`, e.g.
`! python3 <skill-dir>/scripts/publish.py auth youtube`.
`$PUB whoami` shows what is connected.

## Every time: plan → approve in chat → publish

1. **Plan** from a rendered shorts-clipper project:
   ```bash
   $PUB plan "<project>" --platforms youtube,instagram,facebook,linkedin,tiktok \
       --youtube-privacy public --top 5 --start 2026-11-05T18:00:00+06:00 --every 24
   ```
   One entry per clip × platform, best clips first; `--start/--every` spaces them out (no
   `--start` = post now). Or `--slots "mon-fri 18:00, sat+sun 11:00"` (local time) puts each
   clip in the next free posting slot, skipping slots used by other plans in the project.
   Text comes from each pick's `posts.<platform>` (use `youtube-seo` for YouTube
   title/description/tags), else caption + hashtags. **Ask** for YouTube privacy
   (no default) and TikTok mode. Edit `publish_plan.json` for platform-specific wording.
2. **Show & ask.** Offer the calendar: `$PUB calendar <plan> [more plans…]` serves a month view
   at http://127.0.0.1:8790 (local only) — the user can drag posts to other days, click to edit
   text/time, approve or reject; every change is validated and saved to the plan. Approvals made
   there count as the user's approval (`approved_via: "calendar"`). It runs until Ctrl+C, so the
   user should start it themselves (`! python3 <skill-dir>/scripts/publish.py calendar <plan>`)
   or you run it in the background and stop it afterwards. In chat instead:
   `$PUB show <plan>` prints a numbered table. Put it in chat and ask which
   rows to publish ("all", "1,3,5", "only YouTube"…). Mention anything notable: times are in
   the shown timezone, TikTok drafts need a tap in the app, FB Reels must be ≤ 90 s.
3. **Record the decision** exactly as given: `$PUB approve <plan> --ids 1,3,5` (or `--all`,
   `--reject 2`).
4. **Check & dry run**: `$PUB check <plan>` (must be `ok`), then `$PUB run <plan>` shows what
   would happen. Then `$PUB run <plan> --yes`.
   - YouTube (publishAt) and Facebook (> 10 min ahead) are uploaded now and published by the
     platform at the scheduled time.
   - Others wait until due: offer `$PUB scheduler <plan> --install` — ONE cron job (every 10 min)
     that runs `tick`: publishes approved + due entries of registered plans, runs autopilot rules,
     and refreshes analytics every 6 h. `scheduler <plan> --remove` unregisters a plan (the cron
     line goes away when nothing is left). Installing cron changes the user's crontab — ask first.
   - Progress is saved after every post; re-running never double-posts.
5. **Report** `$PUB status <plan>`: published URLs, scheduled items, TikTok drafts waiting in
   the inbox, and errors with the platform's message. To retry an error, fix it and run again.

One-off: `$PUB post --platform youtube --media clip.mp4 --title "…" --privacy unlisted --text "…"`
(dry run; add `--yes` after the user approves).

## Analytics

`$PUB analytics <project-or-plan>` fetches current numbers for every published post, keeps a
history (the cron tick refreshes it every 6 h), and writes `analytics.html` + `analytics.json`
in the project: totals per platform, best clips across platforms, every post with views/likes/
comments and a link. Per platform: YouTube views/likes/comments · Instagram views/reach/likes/
comments/saves/shares · Facebook Reels plays/reach/reactions · TikTok views/likes/comments/shares
(after the draft is published in the app) · Threads views/likes/replies/reposts · Reddit score/
upvote ratio/comments · Bluesky + Mastodon likes/reposts/replies · Pinterest views/impressions/
saves/clicks · Discord reactions. Not available: LinkedIn member posts (restricted API), Telegram
bots, X (paid).
Use the results: tell the user which clips/topics/platforms work, and feed it back into
shorts-clipper picks (similar hooks, lengths, topics) — `best_clips` in analytics.json.

## Autopilot (auto-posting with a standing approval)

When the user wants hands-off posting, create a rule — this IS their approval for every clip
the rule matches, so state the rule back in plain words and get an explicit yes first:
```bash
$PUB autopilot add --project "<project>" --platforms youtube,tiktok,instagram \
    --slots "mon-fri 18:00, sat 11:00" --min-score 70 --max-per-day 1 --youtube-privacy public
$PUB scheduler --install          # the cron tick that runs rules (ask before changing crontab)
```
Each tick: new rendered clips in that project with score ≥ min-score (and no QA issues) are
put in the next free slots (respecting max-per-day), marked `approved_via: autopilot:<rule>`,
and published when due into `publish_plan.autopilot.json` (visible in the calendar). Errors
are recorded and never retried blindly. Everything is logged to `~/.shorts-clipper/social/
autopilot.log`. Manage with `autopilot list | preview <id> | pause <id> | resume <id> |
remove <id>`. `preview` shows what the next tick would schedule without doing it.
Only create rules the user asked for; never widen platforms, privacy or limits on your own.

## Honest limits

- Each platform enforces its own rules and quotas; a self-made app is fine for your own
  accounts but can't post for other people without that platform's review.
- Reddit video submits reply asynchronously; the post is found in the user's submitted
  listing afterwards and is never re-submitted. Posting the same clip to many subreddits
  looks like spam — ask which ONE subreddit fits and check its rules.
- Instagram has no native scheduling (the local scheduler handles it). Instagram/Facebook
  uploads use Meta's resumable upload endpoint; if Meta rejects it for your app type, the
  error says so.
- Mark AI-generated content honestly when it applies (`options.ai_generated` → TikTok
  `is_aigc`, Meta `is_ai_generated`, YouTube `synthetic`).
