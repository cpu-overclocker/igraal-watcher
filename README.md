# iGraal Cashback Watcher

Python bot that scans the iGraal catalog and sends Discord notifications:
- A **daily summary** with the Top 10 cashbacks by % and by EUR
- An **instant notification** whenever an offer reaches **100%** cashback

**Zero dependencies** — only uses Python's standard library. No `pip install`, no virtualenv.

## Requirements

- Python 3.7 or newer ([python.org](https://www.python.org/downloads/))

That's it.

## Quick start

```bash
git clone https://github.com/cpu-overclocker/igraal-watcher.git
cd igraal-watcher
python igraal-watcher.py
```

On the **first launch**, the script will ask you for your Discord webhook URL and save it in `config.json`. You won't be asked again.

## Getting a Discord webhook

1. Open Discord, go to the channel where you want notifications
2. **Channel Settings** (⚙️) → **Integrations** → **Webhooks**
3. **New Webhook** → give it a name (e.g. `iGraal Watcher`)
4. **Copy Webhook URL**
5. Paste it when the script asks for it

### Optional: ping a role on 100% alerts

During setup, the script also asks for an optional "Ping ID". To get it:

1. Enable Developer Mode in Discord: **User Settings** → **Advanced** → **Developer Mode**
2. Right-click the role → **Copy Role ID**
3. Paste it during setup

Leave empty to disable pings.

## Usage

```bash
python igraal-watcher.py
```

### Test mode

To quickly validate on ~50 retailers (about 15 seconds), open `igraal-watcher.py` and set:

```python
TEST_MODE = True
```

Run the script. If everything looks good, set it back to `False` for the full scan.

## Configuration

In `igraal-watcher.py`:

| Parameter | Description | Recommended values |
|---|---|---|
| `MAX_WORKERS` | Parallel requests | 1 (safe), 2 (moderate), 4 (fast) |
| `MIN_DELAY_BETWEEN_REQUESTS` | Min delay between requests | 0.4 / 0.3 / 0.15 |
| `THRESHOLD` | Alert threshold (%) | `100` |
| `TOP_SIZE` | Number of items in Top lists | `10` |
| `TEST_MODE` | Quick validation on 50 retailers | `False` |

### Performance profiles

| Profile | Workers | Delay | Duration (~3565 retailers) |
|---|---|---|---|
| Safe | 1 | 0.4s | ~24 min |
| Moderate | 2 | 0.3s | ~12 min |
| Fast (default) | 4 | 0.15s | ~9 min |

## Automating with Windows Task Scheduler

1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily, pick a time
3. Action: Start a program
   - Program: `python`
   - Arguments: `C:\path\to\igraal-watcher.py`
   - Start in: `C:\path\to\`

## How it works

The script uses the **public GraphQL API** used by the official iGraal browser extension:

1. `RetailersByFilter` — fetches all published retailers (paginated)
2. `ActiveCashbackWithMaxCommission` — fetches the cashback for each retailer

It then filters, sorts, and reports the best offers.

## Disclaimer

This script queries the public API used by the official iGraal extension. It only **reads** offers — it does not automate purchases or affiliate clicks. Check iGraal's Terms of Service before use.

## License

MIT