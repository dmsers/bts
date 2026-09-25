"""
Beat the Streak — Stage 1: Daily Data Ingestion
================================================
Pulls today's schedule, probable pitchers, and caches pitcher/hitter stats.

Usage:
    python ingest.py                    # today's games
    python ingest.py --date 2026-03-26  # specific date
    python ingest.py --refresh-stats    # force refresh season stat caches

Requires:
    conda install -c conda-forge pybaseball mysql-connector-python
    pip install python-dotenv  (optional, for .env support)
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pybaseball import pitching_stats_bref
import mysql.connector
import pandas as pd
import pybaseball
import math
import config

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOGS_DIR / "ingest.log"),
    ],
)
log = logging.getLogger("bts.ingest")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    """Return a MySQL connection using config credentials."""
    return mysql.connector.connect(**config.DB_CONFIG)


def upsert_game(cursor, row: dict):
    """Insert or update a game record."""
    sql = """
        INSERT INTO games
            (game_pk, game_date, away_team, home_team, game_time_et,
             away_pitcher_id, away_pitcher, home_pitcher_id, home_pitcher, status)
        VALUES
            (%(game_pk)s, %(game_date)s, %(away_team)s, %(home_team)s, %(game_time_et)s,
             %(away_pitcher_id)s, %(away_pitcher)s,
             %(home_pitcher_id)s, %(home_pitcher)s, %(status)s)
        ON DUPLICATE KEY UPDATE
            away_team       = VALUES(away_team),
            home_team       = VALUES(home_team),
            away_pitcher_id = VALUES(away_pitcher_id),
            away_pitcher    = VALUES(away_pitcher),
            home_pitcher_id = VALUES(home_pitcher_id),
            home_pitcher    = VALUES(home_pitcher),
            status          = VALUES(status),
            game_time_et    = VALUES(game_time_et)
    """
    cursor.execute(sql, row)


def upsert_pitcher_stats(cursor, row: dict):
    """Insert or update cached pitcher season stats."""
    sql = """
        INSERT INTO pitcher_stats
            (pitcher_id, season, pitcher_name, throws,
             ip, era, whip, k_per_9, bb_per_9, k_pct, bb_pct, ba_against)
        VALUES
            (%(pitcher_id)s, %(season)s, %(pitcher_name)s, %(throws)s,
             %(ip)s, %(era)s, %(whip)s, %(k_per_9)s, %(bb_per_9)s,
             %(k_pct)s, %(bb_pct)s, %(ba_against)s)
        ON DUPLICATE KEY UPDATE
            pitcher_name = VALUES(pitcher_name),
            ip           = VALUES(ip),
            era          = VALUES(era),
            whip         = VALUES(whip),
            k_per_9      = VALUES(k_per_9),
            bb_per_9     = VALUES(bb_per_9),
            k_pct        = VALUES(k_pct),
            bb_pct       = VALUES(bb_pct),
            ba_against   = VALUES(ba_against)
    """
    cursor.execute(sql, row)


def upsert_hitter_stats(cursor, row: dict):
    """Insert or update cached hitter season stats."""
    sql = """
        INSERT INTO hitter_stats
            (hitter_id, season, hitter_name, bats,
             pa, ab, hits, ba, obp, slg, k_pct, contact_pct,
             ba_vs_l, ba_vs_r, games_with_hit, games_played, hit_rate)
        VALUES
            (%(hitter_id)s, %(season)s, %(hitter_name)s, %(bats)s,
             %(pa)s, %(ab)s, %(hits)s, %(ba)s, %(obp)s, %(slg)s,
             %(k_pct)s, %(contact_pct)s, %(ba_vs_l)s, %(ba_vs_r)s,
             %(games_with_hit)s, %(games_played)s, %(hit_rate)s)
        ON DUPLICATE KEY UPDATE
            hitter_name    = VALUES(hitter_name),
            pa             = VALUES(pa),
            ab             = VALUES(ab),
            hits           = VALUES(hits),
            ba             = VALUES(ba),
            obp            = VALUES(obp),
            slg            = VALUES(slg),
            k_pct          = VALUES(k_pct),
            contact_pct    = VALUES(contact_pct),
            ba_vs_l        = VALUES(ba_vs_l),
            ba_vs_r        = VALUES(ba_vs_r),
            games_with_hit = COALESCE(VALUES(games_with_hit), games_with_hit),
            games_played   = VALUES(games_played),
            hit_rate       = COALESCE(VALUES(hit_rate), hit_rate)
    """
    cursor.execute(sql, row)


# ---------------------------------------------------------------------------
# 1A. Pull today's schedule + probable pitchers
# ---------------------------------------------------------------------------

def fetch_schedule(target_date: date) -> pd.DataFrame:
    """
    Pull the MLB schedule for a given date using pybaseball/statsapi.
    Returns a DataFrame with game_pk, teams, probable pitchers.
    """
    log.info(f"Fetching schedule for {target_date}")

    # pybaseball.schedule_and_record is team-level; for a date-level pull,
    # we use the MLB Stats API directly via statsapi
    try:
        import statsapi
    except ImportError:
        log.error("Missing dependency: pip install MLB-StatsAPI")
        raise

    date_str = target_date.strftime("%Y-%m-%d")
    sched = statsapi.schedule(date=date_str)

    games = []
    for g in sched:
        games.append({
            "game_pk":         g["game_id"],
            "game_date":       target_date,
            "away_team":       config.TEAM_ABBR_MAP.get(g.get("away_name", ""), g.get("away_name", "")[:5]),
            "home_team":       config.TEAM_ABBR_MAP.get(g.get("home_name", ""), g.get("home_name", "")[:5]),
            "game_time_et":    _parse_game_time(g.get("game_datetime")),
            "away_pitcher_id": g.get("away_probable_pitcher_id"),
            "away_pitcher":    g.get("away_probable_pitcher", "TBD"),
            "home_pitcher_id": g.get("home_probable_pitcher_id"),
            "home_pitcher":    g.get("home_probable_pitcher", "TBD"),
            "status":          _map_status(g.get("status", "")),
        })

    log.info(f"  Found {len(games)} games")
    return pd.DataFrame(games)


def _parse_game_time(dt_str):
    """Extract time from ISO datetime string."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        # Convert UTC to ET (rough — proper tz handling if needed)
        et = dt - timedelta(hours=4)
        return et.strftime("%H:%M:%S")
    except Exception:
        return None


def _map_status(status_str: str) -> str:
    """Map MLB API status to our enum."""
    s = status_str.lower()
    if "final" in s:
        return "final"
    if "progress" in s or "live" in s:
        return "in_progress"
    if "postpone" in s:
        return "postponed"
    return "scheduled"


def _subtract_hours(time_str: str, hours: int) -> str:
    """Subtract hours from a time string like '13:15:00'."""
    try:
        from datetime import datetime, timedelta
        t = datetime.strptime(time_str, "%H:%M:%S")
        t -= timedelta(hours=hours)
        return t.strftime("%-I:%M %p")
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# ID crosswalk helpers (FanGraphs ↔ Baseball Reference fallback)
# ---------------------------------------------------------------------------

def _load_existing_hitter_ids(cursor) -> dict:
    """Build a name → FG hitter_id map from player_id_map."""
    cursor.execute(
        "SELECT fg_id, player_name FROM player_id_map "
        "WHERE fg_id > 0 AND is_pitcher = 0"
    )
    lookup = {}
    for fg_id, name in cursor.fetchall():
        lookup[name.strip()] = int(fg_id)
    log.info(f"  Loaded {len(lookup)} existing hitter name→FG ID mappings from crosswalk")
    return lookup


def _load_existing_pitcher_ids(cursor) -> dict:
    """Build a name → FG pitcher_id map from player_id_map."""
    cursor.execute(
        "SELECT fg_id, player_name FROM player_id_map "
        "WHERE fg_id > 0 AND is_pitcher = 1"
    )
    lookup = {}
    for fg_id, name in cursor.fetchall():
        lookup[name.strip()] = int(fg_id)
    log.info(f"  Loaded {len(lookup)} existing pitcher name→FG ID mappings from crosswalk")
    return lookup


# ---------------------------------------------------------------------------
# 1B. Refresh pitcher season stats (Statcast)
# ---------------------------------------------------------------------------

def refresh_pitcher_stats(season: int, pitcher_ids: list = None, cursor=None):
    """
    Pull pitcher stats from pybaseball's Statcast data.
    If pitcher_ids is None, refreshes all pitchers who appear in today's games.
    """
    log.info(f"Refreshing pitcher stats for {season}")

    source = "fangraphs"

    # Use pybaseball pitching_stats for season-level stats
    # This pulls from FanGraphs
    try:
        pitching = pybaseball.pitching_stats(season, qual=1)
        log.info(f"  Pulled {season} pitching stats from FanGraphs")
    except Exception as fg_err:
        log.warning(f"  FanGraphs {season} failed: {fg_err}")
        try:
            log.info(f"  Trying Baseball Reference for {season}...")
            pitching = pybaseball.pitching_stats_bref(season)
            source = "bref"
            log.info(f"  Pulled {season} pitching stats from Baseball Reference")
        except Exception as bref_err:
            log.warning(f"  Baseball Reference {season} also failed: {bref_err}")
            try:
                log.info(f"  Trying FanGraphs for {season - 1}...")
                pitching = pybaseball.pitching_stats(season - 1, qual=1)
                log.info(f"  Pulled {season - 1} pitching stats from FanGraphs")
            except Exception as fg2_err:
                log.warning(f"  FanGraphs {season - 1} failed: {fg2_err}")
                try:
                    log.info(f"  Trying Baseball Reference for {season - 1}...")
                    pitching = pybaseball.pitching_stats_bref(season - 1)
                    source = "bref"
                    log.info(f"  Pulled {season - 1} pitching stats from Baseball Reference")
                except Exception as bref2_err:
                    log.warning(f"  All pitcher stat sources failed. Skipping refresh.")
                    return []
                
    if pitching is None or pitching.empty:
        log.warning(f"  No pitching data returned for {season}")
        return []

    log.info(f"  Pulled {len(pitching)} pitcher records (source: {source})")

    # If BR fallback, load existing FG IDs for name-based matching
    fg_lookup = {}
    if source == "bref" and cursor is not None:
        fg_lookup = _load_existing_pitcher_ids(cursor)

    rows = []
    skipped = 0
    for _, p in pitching.iterrows():
        name = p.get("Name", "Unknown").strip()

        if source == "bref":
            # BR doesn't have IDfg — match by name to existing FG IDs
            pid = fg_lookup.get(name)
            if pid is None:
                skipped += 1
                continue
        else:
            pid = p.get("IDfg") or p.get("playerid")

        if pitcher_ids and pid not in pitcher_ids:
            continue

        # Calculate K% and BB% if not directly available
        if source == "bref":
            # BRef uses 'SO' and 'BF' (Batters Faced)
            so = _safe_float(p, "SO")
            bf = _safe_float(p, "BF")
            k_pct = so / bf if (so is not None and bf and bf > 0) else None
            
            bb = _safe_float(p, "BB")
            bb_pct = bb / bf if (bb is not None and bf and bf > 0) else None
            
            # Opponent BA in BRef is often not in this table, but we can try 'BA' or 'BAbip'
            ba_against = _safe_float(p, "BA") or _safe_float(p, "BAbip")
        else:
            k_pct = _safe_pct(p, "K%")
            bb_pct = _safe_pct(p, "BB%")
            ba_against = _safe_float(p, "AVG")

        rows.append({
            "pitcher_id":   int(pid) if pid else 0,
            "season":       season,
            "pitcher_name": name,
            "throws":       "L" if str(p.get("Team", "")).endswith("*") else "R",
            "ip":           _safe_float(p, "IP"),
            "era":          _safe_float(p, "ERA"),
            "whip":         _safe_float(p, "WHIP"),
            "k_per_9":      _safe_float(p, "K/9") or _safe_float(p, "SO9"),
            "bb_per_9":     _safe_float(p, "BB/9"),
            "k_pct":        k_pct,
            "bb_pct":       bb_pct,
            "ba_against":   ba_against,
        })

    if skipped:
        log.warning(f"  Skipped {skipped} BR pitchers with no existing FG ID match")

    return rows


# ---------------------------------------------------------------------------
# 1C. Refresh hitter season stats
# ---------------------------------------------------------------------------

def refresh_hitter_stats(season: int, cursor=None):
    """Pull hitter stats from pybaseball/FanGraphs."""
    log.info(f"Refreshing hitter stats for {season}")

    source = "fangraphs"

    try:
        batting = pybaseball.batting_stats(season, qual=1)
        log.info(f"  Pulled {season} batting stats from FanGraphs")
    except Exception as fg_err:
        log.warning(f"  FanGraphs {season} failed: {fg_err}")
        try:
            log.info(f"  Trying Baseball Reference for {season}...")
            batting = pybaseball.batting_stats_bref(season)
            source = "bref"
            log.info(f"  Pulled {season} batting stats from Baseball Reference")
        except Exception as bref_err:
            log.warning(f"  Baseball Reference {season} also failed: {bref_err}")
            try:
                log.info(f"  Trying FanGraphs for {season - 1}...")
                batting = pybaseball.batting_stats(season - 1, qual=1)
                log.info(f"  Pulled {season - 1} batting stats from FanGraphs")
            except Exception as fg2_err:
                log.warning(f"  FanGraphs {season - 1} failed: {fg2_err}")
                try:
                    log.info(f"  Trying Baseball Reference for {season - 1}...")
                    batting = pybaseball.batting_stats_bref(season - 1)
                    source = "bref"
                    log.info(f"  Pulled {season - 1} batting stats from Baseball Reference")
                except Exception as bref2_err:
                    log.warning(f"  All hitter stat sources failed. Skipping refresh.")
                    return []
                
    if batting is None or batting.empty:
        log.warning(f"  No batting data returned for {season}")
        return []

    log.info(f"  Pulled {len(batting)} hitter records (source: {source})")

    # If BR fallback, load existing FG IDs for name-based matching
    fg_lookup = {}
    if source == "bref" and cursor is not None:
        fg_lookup = _load_existing_hitter_ids(cursor)

    rows = []
    skipped = 0
    for _, h in batting.iterrows():
        name = h.get("Name", "Unknown").strip()

        if source == "bref":
            hid = fg_lookup.get(name)
            if hid is None:
                skipped += 1
                continue
            
            pa = _safe_int(h, "PA")
            so = _safe_int(h, "SO")
            k_pct = so / pa if (so is not None and pa and pa > 0) else None
            contact_pct = 1.0 - k_pct if k_pct is not None else None
            ba = _safe_float(h, "BA")
            hits = _safe_int(h, "H")
            gp = _safe_int(h, "G")
            gwh = _safe_int(h, "games_with_hit")
            hit_rate = gwh / gp if (gwh is not None and gp and gp > 0) else None
        else:
            hid = h.get("IDfg") or h.get("playerid")
            k_pct = _safe_pct(h, "K%")
            contact_pct = 1.0 - k_pct if k_pct is not None else None
            ba = _safe_float(h, "AVG")
            # If FanGraphs provides a hit_rate (it usually doesn't), use it; 
            # otherwise it will be None and we'll compute it from logs later.
            hit_rate = _safe_float(h, "hit_rate")

        # --- Handedness: try multiple FG/BRef column names ---
        bats_raw = (
            h.get("Bats")           # FanGraphs standard
            or h.get("bats")        # lowercase variant
            or h.get("B/T", "")     # BRef combined "R/R" format
        )
        if isinstance(bats_raw, str) and "/" in bats_raw:
            bats_raw = bats_raw.split("/")[0]  # "R/R" → "R"
        bats = bats_raw.strip().upper()[:1] if isinstance(bats_raw, str) and bats_raw.strip() else None
        if bats not in ("L", "R", "S"):
            bats = "R"  # default; corrected later by refresh_platoon_splits()

        rows.append({
            "hitter_id":      int(hid) if hid else 0,
            "season":         season,
            "hitter_name":    name,
            "bats":           bats,
            "pa":             _safe_int(h, "PA"),
            "ab":             _safe_int(h, "AB"),
            "hits":           _safe_int(h, "H"),
            "ba":             ba,
            "obp":            _safe_float(h, "OBP"),
            "slg":            _safe_float(h, "SLG"),
            "k_pct":          k_pct,
            "contact_pct":    contact_pct,
            "ba_vs_l":        None,  # populated by refresh_platoon_splits()
            "ba_vs_r":        None,
            "games_with_hit": None,  # computed from game logs
            "games_played":   _safe_int(h, "G"),
            "hit_rate":       hit_rate,
        })

    if skipped:
        log.warning(f"  Skipped {skipped} BR hitters with no existing FG ID match")

    return rows


# ---------------------------------------------------------------------------
# 1D. Refresh platoon splits (vs LHP / vs RHP)
# ---------------------------------------------------------------------------

def refresh_platoon_splits(season: int, cursor=None):
    """
    Pull batting splits vs LHP and RHP from FanGraphs.
    Updates ba_vs_l and ba_vs_r in hitter_stats for matched players.
    Also backfills the 'bats' column where it's NULL.
    """
    log.info(f"Refreshing platoon splits for {season}")

    updated = 0
    bats_fixed = 0

    # ── Estimate splits from overall BA + handedness ──
    # Average MLB platoon splits (empirical):
    #   RHB vs LHP: +.015 over overall BA
    #   RHB vs RHP: -.010 from overall BA
    #   LHB vs RHP: +.020 over overall BA
    #   LHB vs LHP: -.015 from overall BA
    #   SHB: +.005 vs both (switch-hitter advantage)

    PLATOON_ADJUSTMENTS = {
        "R": {"ba_vs_l": +0.015, "ba_vs_r": -0.010},
        "L": {"ba_vs_l": -0.015, "ba_vs_r": +0.020},
        "S": {"ba_vs_l": +0.005, "ba_vs_r": +0.005},
    }

    if cursor is None:
        log.warning("  No cursor provided — skipping platoon split update")
        return 0

    # First: backfill NULL bats from player_id_map or daily_lineups
    cursor.execute("""
        SELECT hs.hitter_id, hs.season,
               COALESCE(
                   (SELECT dl.bats FROM daily_lineups dl
                    JOIN player_id_map pim ON dl.hitter_id = pim.mlb_id
                    WHERE pim.fg_id = hs.hitter_id AND dl.bats IS NOT NULL
                    LIMIT 1),
                   (SELECT dl.bats FROM daily_lineups dl
                    WHERE dl.hitter_id = hs.hitter_id AND dl.bats IS NOT NULL
                    LIMIT 1)
               ) AS real_bats
        FROM hitter_stats hs
        WHERE hs.season = %s AND (hs.bats IS NULL OR hs.bats = '')
    """, (season,))
    bats_rows = cursor.fetchall()
    for hid, ssn, real_bats in bats_rows:
        if real_bats and real_bats in ("L", "R", "S"):
            cursor.execute("""
                UPDATE hitter_stats SET bats = %s
                WHERE hitter_id = %s AND season = %s
            """, (real_bats, hid, ssn))
            bats_fixed += 1

    if bats_fixed:
        log.info(f"  Backfilled bats handedness for {bats_fixed} hitters from lineup data")

    # Now apply estimated platoon splits where ba_vs_l / ba_vs_r are NULL
    cursor.execute("""
        SELECT hitter_id, ba, bats
        FROM hitter_stats
        WHERE season = %s AND ba IS NOT NULL
          AND (ba_vs_l IS NULL OR ba_vs_r IS NULL)
          AND bats IS NOT NULL AND bats != ''
    """, (season,))
    rows = cursor.fetchall()

    for hid, ba, bats in rows:
        ba = float(ba)
        adj = PLATOON_ADJUSTMENTS.get(bats, PLATOON_ADJUSTMENTS["R"])
        ba_vs_l = round(min(ba + adj["ba_vs_l"], 0.400), 4)
        ba_vs_r = round(min(ba + adj["ba_vs_r"], 0.400), 4)

        cursor.execute("""
            UPDATE hitter_stats
            SET ba_vs_l = %s, ba_vs_r = %s
            WHERE hitter_id = %s AND season = %s
              AND ba_vs_l IS NULL
        """, (ba_vs_l, ba_vs_r, hid, season))
        updated += 1

    log.info(f"  Updated platoon estimates for {updated} hitters")
    return updated


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe_float(row, col):
    try:
        v = row.get(col)
        if v is None or pd.isna(v):
            return None
        f = float(v)
        if math.isinf(f) or math.isnan(f):
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None

def _safe_int(row, col):
    try:
        v = row.get(col)
        if v is None or pd.isna(v):
            return None
        return int(v)
    except (ValueError, TypeError):
        return None

def _safe_pct(row, col):
    """Parse a percentage field like '25.3%' or 0.253."""
    try:
        v = row.get(col)
        if v is None or pd.isna(v):
            return None
        if isinstance(v, str):
            v = v.strip().rstrip("%")
            f = float(v) / 100
        else:
            f = float(v)
            # If > 1, assume it's a percentage (e.g., 25.3)
            if f > 1:
                f = f / 100
        if math.isinf(f) or math.isnan(f):
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


def _schedule_fresh(cursor, target_date: date, max_age_minutes: int = 30) -> bool:
    """Check if schedule was fetched recently enough to skip."""
    cursor.execute(
        "SELECT MAX(updated_at) FROM games WHERE game_date = %s",
        (target_date,)
    )
    result = cursor.fetchone()
    if not result or not result[0]:
        return False
    last_update = result[0]
    if isinstance(last_update, str):
        last_update = datetime.fromisoformat(last_update)
    age = datetime.now() - last_update
    return age.total_seconds() < (max_age_minutes * 60)

# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run(target_date: date, refresh_stats: bool = False):
    """Execute the full ingestion pipeline for a target date."""

    log.info(f"{'='*60}")
    log.info(f"BTS Ingestion — {target_date}")
    log.info(f"{'='*60}")

    conn = get_db()
    cursor = conn.cursor()

    try:
# ----- 1A: Schedule -----
        if _schedule_fresh(cursor, target_date):
            log.info(f"  Schedule fetched recently, skipping API call")
            cursor.execute(
                "SELECT COUNT(*) FROM games WHERE game_date = %s",
                (target_date,)
            )
            game_count = cursor.fetchone()[0]
            log.info(f"  Using {game_count} cached games")

            # Reload schedule for pitcher ID collection below
            cursor.execute("""
                SELECT game_pk, game_date, away_team, home_team, game_time_et,
                       away_pitcher_id, away_pitcher, home_pitcher_id, home_pitcher, status
                FROM games WHERE game_date = %s
            """, (target_date,))
            cols = [d[0] for d in cursor.description]
            schedule_df = pd.DataFrame([dict(zip(cols, r)) for r in cursor.fetchall()])
        else:
            schedule_df = fetch_schedule(target_date)
            for _, g in schedule_df.iterrows():
                upsert_game(cursor, g.to_dict())
            conn.commit()
            log.info(f"  Upserted {len(schedule_df)} games")
            
        # Show earliest game time so user knows when to run lineups.py
        if not schedule_df.empty and "game_time_et" in schedule_df.columns:
            times = schedule_df["game_time_et"].dropna()
            if not times.empty:
                earliest = min(times)
                log.info(f"")
                log.info(f"  ⚾ First pitch: {earliest} ET")
                log.info(f"  📋 Run lineups.py by ~2hrs before: check around {_subtract_hours(earliest, 2)}")
                log.info(f"")

        # ----- 1B: Pitcher stats -----
        # Collect all pitcher IDs from today's games
        pitcher_ids = set()
        for _, g in schedule_df.iterrows():
            if g["away_pitcher_id"]:
                pitcher_ids.add(int(g["away_pitcher_id"]))
            if g["home_pitcher_id"]:
                pitcher_ids.add(int(g["home_pitcher_id"]))

        season = config.CURRENT_SEASON
        if refresh_stats or _stats_stale(cursor, "pitcher_stats", season):
            try:
                p_rows = refresh_pitcher_stats(season, cursor=cursor)
            except Exception as e:
                log.warning(f"  Pitcher stats refresh failed, using existing data: {e}")
                p_rows = []
            for row in p_rows:
                upsert_pitcher_stats(cursor, row)
            conn.commit()
            log.info(f"  Upserted {len(p_rows)} pitcher stat records")
        else:
            log.info("  Pitcher stats cache is current, skipping")

        # ----- 1C: Hitter stats -----
        if refresh_stats or _stats_stale(cursor, "hitter_stats", season):
            try:
                h_rows = refresh_hitter_stats(season, cursor=cursor)
            except Exception as e:
                log.warning(f"  Hitter stats refresh failed, using existing data: {e}")
                h_rows = []
            for row in h_rows:
                upsert_hitter_stats(cursor, row)
            conn.commit()
            log.info(f"  Upserted {len(h_rows)} hitter stat records")
        else:
            log.info("  Hitter stats cache is current, skipping")

        # ----- 1D: Platoon splits -----
        try:
            splits_updated = refresh_platoon_splits(season, cursor=cursor)
            conn.commit()
            if splits_updated:
                log.info(f"  Platoon splits: updated {splits_updated} hitters")
        except Exception as e:
            log.warning(f"  Platoon splits refresh failed (non-fatal): {e}")

        log.info("Ingestion complete!")

    except Exception:
        conn.rollback()
        log.exception("Ingestion failed")
        raise
    finally:
        cursor.close()
        conn.close()


def _stats_stale(cursor, table: str, season: int) -> bool:
    """Check if stats table was updated today."""
    cursor.execute(
        f"SELECT MAX(updated_at) FROM {table} WHERE season = %s",
        (season,)
    )
    result = cursor.fetchone()
    if not result or not result[0]:
        return True
    last_update = result[0]
    if isinstance(last_update, str):
        last_update = datetime.fromisoformat(last_update)
    return last_update.date() < date.today()

def _schedule_fresh(cursor, target_date: date, max_age_minutes: int = 30) -> bool:
    """Check if schedule was fetched recently enough to skip."""
    cursor.execute("""
        SELECT MAX(updated_at) FROM games WHERE game_date = %s
    """, (target_date,))
    result = cursor.fetchone()
    if not result or not result[0]:
        return False
    last_update = result[0]
    if isinstance(last_update, str):
        last_update = datetime.fromisoformat(last_update)
    age = datetime.now() - last_update
    return age.total_seconds() < (max_age_minutes * 60)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BTS Daily Data Ingestion")
    parser.add_argument(
        "--date", type=str, default=None,
        help="Target date (YYYY-MM-DD). Default: today"
    )
    parser.add_argument(
        "--refresh-stats", action="store_true",
        help="Force refresh of season stat caches"
    )
    args = parser.parse_args()

    target = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else date.today()
    )

    run(target, refresh_stats=args.refresh_stats)


if __name__ == "__main__":
    main()