# Dashboard Options — Comparison

| | Flask | Notion | Airtable | Google Sheets | GitHub TSV | Supabase | Datasette |
|---|---|---|---|---|---|---|---|
| **UI quality** | Custom (you build it) | Excellent | Excellent | Spreadsheet | Basic table renderer | Good | Dated but functional |
| **Setup overhead** | High — Flask app + systemd service | Low — API key + `notion-client` | Low — API key + `pyairtable` | Very low — `gspread` | Zero | Low — project + Python client | Medium — self-host or Datasette Cloud |
| **Python update method** | Write JSON files | Notion API (create/update pages) | Airtable REST API | `gspread` | `git commit && git push` | `supabase-py` or REST | Rebuild SQLite file + re-deploy |
| **Auto-refresh** | Yes (meta refresh) | No (manual reload) | No (manual reload) | No (manual reload) | No (manual reload) | No (manual reload) | No (manual reload) |
| **Live prices** | Yes (yfinance on load) | No (snapshot at cycle time) | No | No | No | No | No |
| **Free tier** | Self-hosted (free) | Yes (generous) | Yes (1,000 rows/base) | Yes | Yes | Yes (500MB DB) | Yes (self-host) / paid cloud |
| **External dependency** | No | Yes | Yes | Yes | No (just GitHub) | Yes | Yes (if cloud) |
| **Data history / audit trail** | Manual | Built-in (page history) | Built-in (revision history) | Built-in (version history) | Git history (free) | Full DB history | File-based |
| **Filtering / sorting** | Custom | Yes | Yes | Basic | No | Yes | Yes |
| **Mobile friendly** | Depends on your HTML | Yes | Yes | Partial | Poor | Yes | Partial |
| **Other integrations** | None built-in | Huge ecosystem | Good ecosystem | Google ecosystem | GitHub ecosystem | Postgres ecosystem | Limited |
| **Verdict** | Most control, most work | Best all-round if already using Notion | Best pure table UI | Simplest to code | Zero overhead, no frills | Good if you want a real DB | Promising but behind |

---

## Recommendation

**If you're already using or planning to use Notion for other things → use Notion.**
The API is straightforward, the UI is the best of any option, and the integration cost is effectively zero if you're in that ecosystem anyway.

**If you want absolute zero overhead → GitHub TSV.**
No credentials, no external service, data lives in the repo forever.

**If you want a real queryable database with a decent UI → Supabase.**
Free tier is plenty for this volume, and you get full SQL if you ever need it.
