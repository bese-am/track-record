# Deploying the Besë track record

## The short answer: GitHub Pages. Free, permanently.

Not just because it is the cheapest — because **you already need a public git
repository.** The hash chain is only evidence if a stranger can clone the record
and re-verify it, and branch protection (no force-push, no deletion on `main`) is
what stops the history being quietly rewritten. That is a GitHub feature.

So GitHub Pages does not add a hosting dependency. It makes the repository you
already needed serve itself. One thing to set up, one thing to trust, one place
where the record lives.

| | Cost | Commercial use | Notes |
|---|---|---|---|
| **GitHub Pages** | **£0** | Allowed on public repos | 100 GB/month, 1 GB site, custom domain + HTTPS free, no usage billing |
| Cloudflare Pages | £0 | Allowed | Unlimited static bandwidth; the upgrade if you ever outgrow Pages |
| Netlify | £0 | Allowed | Credit-based free tier, can be exhausted |
| Vercel | £0 → **$20/mo** | **Hobby plan prohibits commercial use** | Where RVB is hosted; a firm publishing a track record is not a hobby project |

The site is 168 KB of static HTML with no build step, no runtime and no secret.
Any of these would serve it. GitHub Pages is the one that also solves
verification.

Only real cost: a domain, roughly £10–15/year, and it is optional —
`username.github.io/bese-track-record` works and is free.

## If you are on Vercel

Vercel serves the repository root by default, and the root has no `index.html` —
the site lives in `docs/`. That is almost certainly the 404. `vercel.json` at the
project root fixes it:

```json
{
  "framework": null,          // "Other" preset — stop it guessing
  "buildCommand": null,       // there is nothing to build
  "installCommand": null,     // and nothing to install
  "outputDirectory": "docs"   // serve this
}
```

Commit it and redeploy. If the dashboard has a **Root Directory** set under
Settings → Build & Deployment, clear it — it is applied before `outputDirectory`
and the two will fight.

The shipped file also sets `Access-Control-Allow-Origin: *` on `/data/`, so
anyone can fetch the record cross-origin and re-verify it without cloning, and
serves `CHAIN.jsonl` and the CSVs as `text/plain` so they open in a browser
rather than downloading.

One thing to keep in view: **Vercel's Hobby plan is for personal projects and
prohibits commercial use.** Fine while this is a private experiment. The day the
site is doing a job for the firm, it is $20/month on Pro — or free on GitHub
Pages or Cloudflare Pages, both of which allow commercial use.

## Setup, once

**1. Create a public repository** on GitHub — `bese-track-record`.

**2. Push.**

```bash
cd /path/to/bese
git init -b main
git add -A
git commit -m "Besë track record: publisher, metric engine and site"
git remote add origin https://github.com/<you>/bese-track-record.git
git push -u origin main
```

**3. Turn on Pages.** Settings → Pages → Source: *Deploy from a branch* →
Branch `main`, folder **`/docs`**. Live in about a minute at
`https://<you>.github.io/bese-track-record/`.

The publisher writes the site into `docs/` for exactly this reason: Pages serves
it straight off `main` with no second branch, no Actions workflow and no build.

**4. Protect the branch** — this is the part that makes the chain mean something.
Settings → Rules → Rulesets → New branch ruleset, targeting `main`:

- Restrict deletions ✓
- Block force pushes ✓

Without these, whoever controls the repository can rewrite history and the chain
proves less than it appears to. The Verify page says so; make it untrue.

**5. Custom domain** (optional). Put the hostname in a `CNAME` file at the
project root — the publisher copies it into `docs/` on every run, so a rebuild
never wipes it:

```bash
echo "trackrecord.bese.example" > CNAME
```

Then at your registrar, a `CNAME` record pointing at `<you>.github.io`, and in
Settings → Pages set the custom domain and tick *Enforce HTTPS*. The certificate
is issued automatically and free.

## Publishing after that

```bash
python3 autopublish.py --push
```

Ingests whatever is in `data/inbox/`, rebuilds the record, extends the chain,
re-renders the site, commits and pushes. GitHub Pages redeploys on the push.
Add it to cron and the whole thing runs unattended:

```cron
15 22 * * 1-5  cd /path/to/bese && /usr/bin/python3 autopublish.py --push >> data/cron.log 2>&1
```

## Two notes that will save you an afternoon

**`.nojekyll` is written automatically and must stay.** GitHub Pages runs Jekyll
over the directory by default, which silently drops paths beginning with an
underscore and rewrites files it thinks it understands. The record has to be
served byte-for-byte as published or the hashes on the Verify page stop matching.

**One repository, not two.** RVB splits data and site. Keeping them together is
better here: the chain lives in the git history, and one repository means one
protected history covering both the record and the code that produced it. The
site ships a copy of the data it renders at `/data/`, so "check it yourself" is a
link rather than a request.

## Timestamping

Install the client on the publishing machine and the publisher does the rest:

```bash
pip install opentimestamps-client
python3 autopublish.py            # stamps new snapshots, upgrades pending ones
```

Each snapshot gets a `.ots` proof beside it, committed with the record. A fresh
proof commits to a calendar server and is *incomplete* until the aggregating
Bitcoin transaction confirms, normally within a few hours; every later run runs
`ots upgrade` to complete it. Free — the calendar servers pay the transaction fee
and aggregate thousands of hashes into one.

Anyone can check a proof without you:

```bash
ots verify books/bese_nominal_100k/snapshots/2026-08-14.json.ots
```

**Stamp on the day.** The proof's value is the tightness of the window between
the session closing and the record being anchored. Publishing nightly from cron
makes that window hours; publishing a month late makes the proof nearly
worthless, because by then you already knew how the month went.

Until the client is installed, `meta.json` carries
`timestamping.available: false` and the Verify page says the dates are claimed
rather than proven. Nothing pretends a proof exists.
