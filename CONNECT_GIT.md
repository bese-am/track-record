# Connecting the repository

**The one part I cannot do.** Pushing to GitHub needs your credentials, and the
code is in my sandbox rather than on your machine. Linking Vercel to the repo I
*can* do — but only once the repo exists on GitHub. So: three commands from you,
then tell me and I finish it.

Everything else is ready. `bese-track-record.tar.gz` carries the full git history
— four commits, working tree clean, `.gitignore`, `vercel.json` and
`.vercelignore` all committed. You do not need to `git init` or re-commit
anything.

## Your part

**1. Extract, keeping the history.**

```bash
tar xzf bese-track-record.tar.gz
cd bese
git log --oneline          # should show 4 commits, most recent first
```

**2. Create an EMPTY public repository** on GitHub called `bese-track-record`.
No README, no .gitignore, no licence — an empty repo, or the first push will
conflict.

Public matters: the hash chain is only evidence if a stranger can clone the
record and re-verify it. A private repo makes the Verify page a claim about
files nobody can read.

**3. Push.**

```bash
git remote add origin https://github.com/<you>/bese-track-record.git
git push -u origin main
```

With the GitHub CLI, steps 2 and 3 are one command:

```bash
gh repo create bese-track-record --public --source=. --push
```

## Then tell me, and I will

- link the Vercel project to the repo, so every push redeploys
- confirm the link took, and that the first git-driven deployment is `READY`
- check `/data/` is finally being served — it has never been deployed, because
  hand-copying hash-bearing files through a tool call is not a channel I trust
  for the one thing on this site that has to be exact

## And one thing only you can do, in the GitHub UI

Settings → Rules → Rulesets → New branch ruleset, targeting `main`:

- **Restrict deletions** ✓
- **Block force pushes** ✓

Do this before you show the record to anyone. Without it, whoever controls the
repository can rewrite history, and the Verify page's own text says so —
*"Git history can be rewritten by whoever controls the repository, which is why
the hash chain is used alongside branch protection rather than instead of it."*
Branch protection is the half that makes that sentence true.

## What changes once it is connected

```bash
python3 autopublish.py --push
```

Ingests whatever is in `data/inbox/`, rebuilds the record from the whole
archive, extends the chain, re-renders the site, commits, pushes — and Vercel
redeploys on the push. Add the cron line from DEPLOY.md and the whole thing runs
without you.

Note that `data/archive/` is gitignored on purpose: it holds the raw exports,
which carry the firm's account identifier. Their hashes go into
`archive_manifest.json`, which *is* committed, so the exports behind the record
are fixed in time without being published.
