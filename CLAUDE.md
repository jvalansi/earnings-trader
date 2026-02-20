# CLAUDE.md

## Project Layout

Source files live in `src/`. Always run from the project root with `PYTHONPATH=src`:

```bash
PYTHONPATH=src /home/ubuntu/miniconda3/envs/earnings-trader/bin/python src/main.py
```

For one-off checks:
```bash
cd /home/ubuntu/earnings-trader
PYTHONPATH=src /home/ubuntu/miniconda3/envs/earnings-trader/bin/python -c "from data.prices import get_atr; print(get_atr('AAPL'))"
```

Runtime data files (`data/positions.json`, `data/trades_log.jsonl`) are created at the project root automatically.

---

## GitHub Access

The GitHub personal access token is stored in `~/.bashrc` as `$GITHUB_TOKEN`.

To push to GitHub, source it and use it in the remote URL:

```bash
GITHUB_TOKEN=$(grep GITHUB_TOKEN ~/.bashrc | grep -oP 'ghp_\w+')
git remote set-url origin https://$GITHUB_TOKEN@github.com/jvalansi/earnings-trader.git
git push
```

Run this before any `git push` if the remote is not already authenticated.

## Workflow

Always commit **and push** after making changes. Never leave commits unpushed.
