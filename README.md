# BTS

BTS produces a ranked list of recommended picks for MLB's **Beat the Streak**.
Each day it pulls the schedule, probable pitchers, and confirmed starting lineups,
scores every starting hitter against the opposing probable pitcher, and emails a
recommendation.

*Not affiliated with or endorsed by Major League Baseball.*

## How It Runs

A single cron entry starts `orchestrate.py` at 8:00 AM. It runs the full day and
stops on its own; no other scheduling is needed.

**Daily order:**

1. `verify_results.py` checks how yesterday's picks did.
2. `backfill_results.py --days 2` updates each hitter's actual games-with-a-hit rate.
3. `ingest.py` pulls today's schedule, probable pitchers, and season stats.
4. Waits until 2 hours before the first game.
5. Hourly loop: `lineups.py` (confirmed lineups), then `recommend.py --top 15`.
   - **LOCK IN**: a strong pick is found. Emails a LOCK IN alert and stops.
   - **WAIT**: emails a WAIT alert (first time only) and checks again in an hour.
   - **6:00 PM hard stop**: emails a FINAL DECISION with the best available picks and stops.

Example crontab entry (macOS):

```
0 8 * * * cd /path/to/bts && /usr/bin/caffeinate -i /path/to/python3 orchestrate.py >> logs/orchestrator.log 2>&1
```

`caffeinate -i` keeps the Mac awake while the orchestrator runs. Cron does not
run jobs that were missed while the machine was asleep.

## Recommendation Method

BTS uses a **rules-based (heuristic) model**, not a trained machine-learning model.
For each hitter in each game, `estimate_hit_probability()` in `recommend.py`
estimates the probability of getting at least one hit, then ranks hitters by that
probability.

### 1. Per–at-bat hit probability

Starts from the hitter's batting average and multiplies it by adjustment factors:

| Factor | What it measures | Range |
|---|---|---|
| Base BA | Hitter's BA vs. the pitcher's hand (falls back to overall BA, then league average) | — |
| Pitcher factor | Pitcher's BA-against vs. league average, adjusted for strikeout rate | K adj. 0.88–1.06 |
| Team factor | Opposing staff's average WHIP | 0.95–1.05 |
| Platoon bonus | Opposite-hand matchup ×1.05, same-hand ×0.96, switch hitter ×1.03 | 0.96–1.05 |
| Contact bonus | Hitter's strikeout rate vs. 20% | ≥ 0.82 |
| Recent form | Share of recent games with a hit (neutral at 70%), +2% for a 3+ game streak | 0.94–1.06 |
| Sample-size confidence | Slight discount for hitters with few plate appearances | 0.97–1.00 |
| Park factor | Ballpark's effect on hits | varies |

The result is capped at .450 per at-bat.

### 2. Per-game probability

Expected plate appearances come from batting-order position, converted to expected
at-bats using the hitter's AB/PA ratio and the pitcher's walk rate. Then:

    P(at least 1 hit) = 1 − (1 − p_hit_per_AB) ^ expected_AB

### 3. Blend with actual hit rate

For hitters with 15+ games played, the model's estimate is blended with the
hitter's actual share of games with a hit this season. The actual rate gets
30–60% weight, increasing with games played. `hit_rate` is maintained daily by
`backfill_results.py`.

### Limitations

- Weights are hand-tuned, not fit to historical data.
- Platoon splits are estimated from overall BA (see `ingest.py`), and a platoon
  bonus is applied on top, so handedness is effectively counted twice.
- Pitcher handedness is not reliably captured; most pitchers are treated as right-handed.
- FanGraphs has returned 403 to pybaseball since at least mid-September 2026, so
  season stats come from Baseball Reference. Baseball Reference records are matched
  by name, and some players are skipped.
- Recently debuted players may not yet have a FanGraphs ID in the Chadwick register
  and can't be mapped until the register is updated.
- If current-season stats can't be pulled from either source, last season's stats
  are stored under the current season.
- UTC-to-Eastern time conversion (`lineups.py`, `ingest.py`) is fixed at −4 hours
  (daylight time) and will be off by one hour outside daylight saving time.
- Each team's lineup is stored as soon as it is posted to the MLB Stats API. The
  "Still waiting on lineups" list in `lineups.py --show` only lists games where
  neither team has posted.
- The 6:00 PM hard stop uses the machine's local clock.

### Future Work

Intermediate model inputs are logged with each recommendation so a learned model
(e.g., logistic regression or gradient boosting) can later be trained and compared
against this baseline.

## 2026 Season Analysis

[docs/BTS_2026_Factor_Breakdown.md](docs/BTS_2026_Factor_Breakdown.md) reviews the 2026
season: how the model changed version by version, which inputs (pitcher matchups,
strikeout rates, lineups, ballparks) actually moved the hit rate, and which inputs
weren't working. `export_factor_data.py` produces the data it uses.

## Database Tables

| Table | Purpose |
|---|---|
| `games` | Daily schedule and probable pitchers (`ingest.py`) |
| `daily_lineups` | Confirmed starting lineups (`lineups.py`) |
| `hitter_stats`, `pitcher_stats` | Season stats (`ingest.py`); `hit_rate` from `backfill_results.py` |
| `park_factors` | Ballpark hit factors |
| `player_id_map` | MLB ↔ FanGraphs ID crosswalk (`crosswalk.py`) |
| `results` | Every hitter's daily outcome (`backfill_results.py`); feeds recent form and hit rate |
| `recommendations` | Daily ranked picks and outcomes (`recommend.py`, `verify_results.py`) |
| `prediction_features` | Model inputs logged per pick, for future model training |
| `predictions`, `streak_log` | Reserved from an earlier model-based design (MLP/LSTM/ensemble scores); not currently used |

## Requirements

- Python 3.9+
- MySQL or MariaDB
- Python packages: `MLB-StatsAPI`, `pybaseball`, `mysql-connector-python`,
  `pandas`, `python-dotenv`

## Installation

```
git clone https://github.com/<your-username>/bts.git
cd bts
pip install MLB-StatsAPI pybaseball mysql-connector-python pandas python-dotenv
cp .env.example .env        # then fill in your values
mysql -u <user> -p <database> < migrations/000_schema.sql
```

## Usage

```
python orchestrate.py              # full daily run (waits for lineups)
python orchestrate.py --now        # skip the wait and start checking immediately
python ingest.py                   # schedule, pitchers, and stats only
python ingest.py --refresh-stats   # force a season stats refresh
python lineups.py                  # fetch today's lineups
python lineups.py --show           # show stored lineups
python recommend.py --top 15       # rank today's picks
```

Most scripts accept `--date YYYY-MM-DD`.

## Configuration

Copy `.env.example` to `.env` and fill in:

```
# Database (MySQL)
BTS_DB_HOST=
BTS_DB_PORT=3306
BTS_DB_USER=
BTS_DB_PASS=
BTS_DB_NAME=

# Email alerts (orchestrate.py)
BTS_SMTP_SERVER=
BTS_SMTP_PORT=587
BTS_SMTP_USER=
BTS_SMTP_PASS=
BTS_EMAIL_RECIPIENT=
```

`config.py` sets strict SQL mode (`STRICT_TRANS_TABLES`) for BTS connections, so
invalid values raise errors instead of being silently truncated or blanked.

To refresh the player ID crosswalk (recommended every few weeks during the season):

    python crosswalk.py --update --all

## Data Sources & Acknowledgments

- Schedules, probable pitchers, lineups, and results from the **MLB Stats API**,
  accessed via [MLB-StatsAPI](https://github.com/toddrob99/MLB-StatsAPI).
  Use is subject to MLB's terms of use.
- Season stats from **FanGraphs** and **Baseball Reference** (Sports Reference),
  accessed via [pybaseball](https://github.com/jldbc/pybaseball). Please respect
  each site's terms and request limits.
- Player ID crosswalk data from the
  [Chadwick Baseball Bureau Register](https://github.com/chadwickbureau/register),
  licensed under the
  [Open Data Commons Attribution License v1.0](http://opendatacommons.org/licenses/by/1.0/).
- BTS was built with help from [Claude](https://claude.ai) by Anthropic,
  which assisted throughout the project, from the code and debugging to the
  2026 season analysis. Google's [Gemini](https://gemini.google.com) also
  contributed, including a round of model tuning in May 2026.

## License

MIT (see LICENSE)