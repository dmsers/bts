"""
BTS - Automation Orchestrator
=============================
1. Runs ingest.py
2. Schedules lineups.py and recommend.py based on earliest game time
3. Emails the results
"""
import subprocess
import smtplib
import os
from email.mime.text import MIMEText
from datetime import datetime, date, timedelta
import time
import logging
import sys
import mysql.connector
import config
import argparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bts.orchestrator")

EMAIL_RECIPIENT = os.getenv("BTS_EMAIL_RECIPIENT")

def run_script(script_name, args=[]):
    log.info(f"Running {script_name} {' '.join(args)}...")
    result = subprocess.run([sys.executable, script_name] + args, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(f"Error running {script_name}: {result.stderr}")
    return result.stdout

def get_earliest_game_time(target_date):
    conn = mysql.connector.connect(**config.DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(game_time_et) FROM games WHERE game_date = %s", (target_date,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else None

def send_email(subject, body, recipient=None):
    if recipient is None:
        recipient = EMAIL_RECIPIENT

    # Load credentials from environment
    smtp_server = os.getenv("BTS_SMTP_SERVER")
    smtp_port = int(os.getenv("BTS_SMTP_PORT", 587))
    smtp_user = os.getenv("BTS_SMTP_USER")
    smtp_pass = os.getenv("BTS_SMTP_PASS")

    if not all([smtp_server, smtp_user, smtp_pass]):
        log.warning("SMTP credentials missing from .env. Printing report to log instead.")
        log.info(f"SUBJECT: {subject}\nCONTENT:\n{body}")
        return

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = recipient

    try:
        if smtp_port == 465:
            # SMTPS (SSL/TLS from the start)
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            # SMTP with STARTTLS
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        log.info(f"Email sent successfully to {recipient}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")
        log.info(f"CONTENT:\n{body}")

def analyze_strategy(recs_text):
    """
    Parses the recommendation text and applies the 'Baseline vs Upside' framework.
    Returns (status_code, report_text)
    """
    lines = recs_text.split('\n')
    confirmed_picks = []
    projected_picks = []
    
    for line in lines:
        if '📋' in line:
            confirmed_picks.append(line)
        elif '📊' in line:
            projected_picks.append(line)
            
    strategy = "\n" + "="*60 + "\n"
    strategy += "  STRATEGIC DECISION FRAMEWORK\n"
    strategy += "="*60 + "\n\n"
    
    status_code = "WAIT"
    
    if not confirmed_picks and projected_picks:
        status_code = "WAIT"
        strategy += "  STATUS: [ WAIT ]\n"
        strategy += "  Reason: No lineups are confirmed yet. Your top projected picks\n"
        strategy += "  offer significantly higher upside than taking a random early starter.\n"
    elif confirmed_picks:
        try:
            best_conf_pct = float(confirmed_picks[0].split('%')[0].split()[-1]) / 100
            best_proj_pct = 0
            if projected_picks:
                best_proj_pct = float(projected_picks[0].split('%')[0].split()[-1]) / 100
            
            delta = best_proj_pct - best_conf_pct
            
            if best_conf_pct >= 0.82:
                status_code = "LOCK IN"
                strategy += "  STATUS: [ LOCK IN ]\n"
                strategy += f"  Reason: You have a Tier A pick ({best_conf_pct:.1%}) confirmed.\n"
                strategy += "  The risk of waiting for later lineups outweighs any marginal gain.\n"
            elif delta > 0.08:
                status_code = "WAIT"
                strategy += "  STATUS: [ WAIT ]\n"
                strategy += f"  Reason: Your best confirmed pick is {best_conf_pct:.1%}, but a\n"
                strategy += f"  projected late pick offers {delta*100:+.1f}% upside. High risk, high reward.\n"
            else:
                status_code = "LOCK IN"
                strategy += "  STATUS: [ LOCK IN ]\n"
                strategy += f"  Reason: Your best confirmed pick ({best_conf_pct:.1%}) is solid.\n"
                strategy += "  Later projections don't offer enough 'Delta' to justify the wait.\n"
        except:
            status_code = "MANUAL"
            strategy += "  STATUS: [ MANUAL REVIEW REQUIRED ]\n"
            strategy += "  Reason: Could not auto-parse probabilities.\n"
    else:
        status_code = "NO_DATA"
        strategy += "  STATUS: [ NO DATA ]\n"
        strategy += "  Reason: No recommendations were generated.\n"
        
    return status_code, strategy

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="Run immediately without initial wait")
    parser.add_argument("--date", type=str, default=None, help="Target date (YYYY-MM-DD)")
    args = parser.parse_args()

    target_date_obj = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    target_date_str = str(target_date_obj)

    # 0. Verify previous day's results and backfill league-wide stats
    log.info("Checking previous day's performance...")
    verify_output = run_script("verify_results.py")
    print(verify_output)
    
    log.info("Backfilling league-wide results...")
    run_script("backfill_results.py", ["--days", "2"])

    # 1. Ingest
    run_script("ingest.py", ["--date", target_date_str])
    
    # 2. Determine schedule
    earliest = get_earliest_game_time(target_date_obj)
    if not earliest:
        log.warning(f"No games found for {target_date_str}.")
        return

    now = datetime.now()
    game_dt = datetime.combine(target_date_obj, (datetime.min + earliest).time())
    lineup_time = game_dt - timedelta(hours=2)
    
    # Hard Stop: 6:00 PM ET
    hard_stop_time = datetime.combine(target_date_obj, datetime.strptime("18:00", "%H:%M").time())
    
    if not args.now and now < lineup_time:
        log.info(f"Earliest game: {game_dt}. Scheduling first check for {lineup_time}.")
        wait_seconds = (lineup_time - now).total_seconds()
        if wait_seconds > 0:
            log.info(f"Waiting {wait_seconds/3600:.2f} hours until first check...")
            time.sleep(wait_seconds)
    
    # 3. The Re-Polling Loop
    sent_initial_wait = False
    
    while True:
        now = datetime.now()
        log.info(f"Checking lineups and recommendations at {now.strftime('%H:%M')}...")
        
        run_script("lineups.py", ["--date", target_date_str])
        recommend_output = run_script("recommend.py", ["--date", target_date_str, "--top", "15"])
        
        status, strategy_report = analyze_strategy(recommend_output)
        full_report = strategy_report + "\n" + recommend_output
        
        if status == "LOCK IN":
            log.info("Found a LOCK IN candidate. Sending email and stopping.")
            send_email(f"BTS LOCK IN ALERT - {target_date_str}", full_report)
            break
            
        if now >= hard_stop_time:
            log.info("Hard stop reached. Sending final report.")
            send_email(f"BTS FINAL DECISION - {target_date_str}", full_report)
            break
            
        if status == "WAIT" and not sent_initial_wait:
            log.info("Initial check resulted in WAIT. Sending alert and starting re-poll.")
            send_email(f"BTS WAIT ALERT - {target_date_str}", full_report)
            sent_initial_wait = True
        
        log.info("Status is WAIT. Re-polling in 1 hour...")
        time.sleep(3600) # Wait 1 hour between checks


if __name__ == "__main__":
    main()
