# CLAUDE.md

## GitHub Access

The GitHub personal access token is stored in `~/.bashrc` as `$GITHUB_TOKEN`.

To push to GitHub, source it and use it in the remote URL:

```bash
GITHUB_TOKEN=$(grep GITHUB_TOKEN ~/.bashrc | grep -oP 'ghp_\w+')
git remote set-url origin https://$GITHUB_TOKEN@github.com/jvalansi/earnings-trader.git
git push
```

Run this before any `git push` if the remote is not already authenticated.
