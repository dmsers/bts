# ************************************************************
# Sequel Ace SQL dump
# Version 20099
#
# https://sequel-ace.com/
# https://github.com/Sequel-Ace/Sequel-Ace
#
# Host:
# Database:
# Generation Time: 2026-09-25 20:02:23 +0000
# ************************************************************


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
SET NAMES utf8mb4;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE='NO_AUTO_VALUE_ON_ZERO', SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;


# Dump of table daily_lineups
# ------------------------------------------------------------

CREATE TABLE `daily_lineups` (
  `id` int NOT NULL AUTO_INCREMENT,
  `game_pk` int NOT NULL,
  `game_date` date NOT NULL,
  `team_abbr` varchar(5) NOT NULL,
  `hitter_id` int NOT NULL,
  `hitter_name` varchar(80) NOT NULL,
  `batting_order` tinyint NOT NULL,
  `position` varchar(5) DEFAULT NULL,
  `bats` enum('L','R','S') DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_game_hitter` (`game_pk`,`hitter_id`),
  KEY `idx_date` (`game_date`),
  KEY `idx_hitter` (`hitter_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table games
# ------------------------------------------------------------

CREATE TABLE `games` (
  `game_pk` int NOT NULL,
  `game_date` date NOT NULL,
  `away_team` varchar(5) NOT NULL,
  `home_team` varchar(5) NOT NULL,
  `game_time_et` time DEFAULT NULL,
  `away_pitcher_id` int DEFAULT NULL,
  `away_pitcher` varchar(80) DEFAULT NULL,
  `home_pitcher_id` int DEFAULT NULL,
  `home_pitcher` varchar(80) DEFAULT NULL,
  `status` enum('scheduled','in_progress','final','postponed') DEFAULT 'scheduled',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`game_pk`),
  KEY `idx_date` (`game_date`),
  KEY `idx_date_status` (`game_date`,`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table hitter_stats
# ------------------------------------------------------------

CREATE TABLE `hitter_stats` (
  `hitter_id` int NOT NULL,
  `season` smallint NOT NULL,
  `hitter_name` varchar(80) NOT NULL,
  `team` varchar(5) DEFAULT NULL,
  `bats` enum('L','R','S') NOT NULL,
  `pa` int DEFAULT '0',
  `ab` int DEFAULT '0',
  `hits` int DEFAULT '0',
  `ba` decimal(5,3) DEFAULT NULL,
  `obp` decimal(5,3) DEFAULT NULL,
  `slg` decimal(5,3) DEFAULT NULL,
  `k_pct` decimal(5,3) DEFAULT NULL,
  `contact_pct` decimal(5,3) DEFAULT NULL,
  `ba_vs_l` decimal(5,3) DEFAULT NULL,
  `ba_vs_r` decimal(5,3) DEFAULT NULL,
  `games_with_hit` int DEFAULT '0',
  `games_played` int DEFAULT '0',
  `hit_rate` decimal(5,3) DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`hitter_id`,`season`),
  KEY `idx_name` (`hitter_name`),
  KEY `idx_hit_rate` (`season`,`hit_rate` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table park_factors
# ------------------------------------------------------------

CREATE TABLE `park_factors` (
  `park_id` varchar(30) NOT NULL,
  `team_abbr` varchar(5) NOT NULL,
  `park_name` varchar(100) NOT NULL,
  `hr_factor` decimal(4,2) DEFAULT '1.00',
  `hit_factor` decimal(4,2) DEFAULT '1.00',
  `run_factor` decimal(4,2) DEFAULT '1.00',
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`park_id`),
  KEY `idx_team` (`team_abbr`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table pitcher_stats
# ------------------------------------------------------------

CREATE TABLE `pitcher_stats` (
  `pitcher_id` int NOT NULL,
  `season` smallint NOT NULL,
  `pitcher_name` varchar(80) NOT NULL,
  `throws` enum('L','R') NOT NULL,
  `ip` decimal(6,1) DEFAULT '0.0',
  `era` decimal(5,2) DEFAULT NULL,
  `whip` decimal(5,3) DEFAULT NULL,
  `k_per_9` decimal(5,2) DEFAULT NULL,
  `bb_per_9` decimal(5,2) DEFAULT NULL,
  `hr_per_9` decimal(5,2) DEFAULT NULL,
  `k_pct` decimal(5,3) DEFAULT NULL,
  `bb_pct` decimal(5,3) DEFAULT NULL,
  `ba_against` decimal(5,3) DEFAULT NULL,
  `whip_l` decimal(5,3) DEFAULT NULL,
  `whip_r` decimal(5,3) DEFAULT NULL,
  `ba_against_l` decimal(5,3) DEFAULT NULL,
  `ba_against_r` decimal(5,3) DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`pitcher_id`,`season`),
  KEY `idx_name` (`pitcher_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table player_id_map
# ------------------------------------------------------------

CREATE TABLE `player_id_map` (
  `mlb_id` int NOT NULL,
  `fg_id` int DEFAULT NULL,
  `player_name` varchar(80) NOT NULL,
  `mlb_name` varchar(80) DEFAULT NULL,
  `fg_name` varchar(80) DEFAULT NULL,
  `match_method` enum('exact','fuzzy','manual','chadwick_id','chadwick_name') DEFAULT 'exact',
  `is_pitcher` tinyint(1) DEFAULT '0',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`mlb_id`),
  KEY `idx_fg` (`fg_id`),
  KEY `idx_name` (`player_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table prediction_features
# ------------------------------------------------------------

CREATE TABLE `prediction_features` (
  `id` int NOT NULL AUTO_INCREMENT,
  `recommendation_id` int NOT NULL,
  `game_pk` int DEFAULT NULL,
  `game_date` date NOT NULL,
  `hitter_id` int DEFAULT NULL,
  `ba_used` float DEFAULT NULL,
  `hitter_bats` varchar(1) DEFAULT NULL,
  `hitter_k_pct` float DEFAULT NULL,
  `hitter_pa` float DEFAULT NULL,
  `games_played` int DEFAULT NULL,
  `hit_rate_blend` float DEFAULT NULL,
  `pitcher_throws` varchar(1) DEFAULT NULL,
  `pitcher_whip` float DEFAULT NULL,
  `pitcher_era` float DEFAULT NULL,
  `pitcher_baa` float DEFAULT NULL,
  `pitcher_k_pct` float DEFAULT NULL,
  `pitcher_matched` tinyint(1) DEFAULT NULL,
  `batting_order` int DEFAULT NULL,
  `park_hit_factor` float DEFAULT NULL,
  `team_factor` float DEFAULT NULL,
  `has_lineup` tinyint(1) DEFAULT NULL,
  `is_doubleheader` tinyint(1) DEFAULT NULL,
  `recent_form_games` int DEFAULT NULL,
  `recent_form_hits` int DEFAULT NULL,
  `recent_form_streak` int DEFAULT NULL,
  `pitcher_factor` float DEFAULT NULL,
  `platoon_bonus` float DEFAULT NULL,
  `contact_bonus` float DEFAULT NULL,
  `form_bonus` float DEFAULT NULL,
  `pa_confidence` float DEFAULT NULL,
  `p_hit_per_ab` float DEFAULT NULL,
  `expected_pa` float DEFAULT NULL,
  `expected_ab` float DEFAULT NULL,
  `p_at_least_1` float DEFAULT NULL,
  `tier` varchar(1) DEFAULT NULL,
  `rank` int DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `recommendation_id` (`recommendation_id`),
  KEY `idx_game_date` (`game_date`),
  KEY `idx_hitter` (`hitter_id`),
  CONSTRAINT `prediction_features_ibfk_1` FOREIGN KEY (`recommendation_id`) REFERENCES `recommendations` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table predictions
# ------------------------------------------------------------

CREATE TABLE `predictions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `game_date` date NOT NULL,
  `game_pk` int NOT NULL,
  `hitter_id` int NOT NULL,
  `hitter_name` varchar(80) NOT NULL,
  `team_abbr` varchar(5) NOT NULL,
  `opp_pitcher_id` int DEFAULT NULL,
  `opp_pitcher` varchar(80) DEFAULT NULL,
  `p_hit_per_pa` decimal(5,4) DEFAULT NULL,
  `expected_pa` decimal(3,1) DEFAULT NULL,
  `p_at_least_1` decimal(5,4) DEFAULT NULL,
  `lstm_score` decimal(5,4) DEFAULT NULL,
  `mlp_score` decimal(5,4) DEFAULT NULL,
  `ensemble_score` decimal(5,4) DEFAULT NULL,
  `pitcher_whip` decimal(5,3) DEFAULT NULL,
  `pitcher_throws` enum('L','R') DEFAULT NULL,
  `hitter_bats` enum('L','R','S') DEFAULT NULL,
  `batting_order` tinyint DEFAULT NULL,
  `park_hit_factor` decimal(4,2) DEFAULT NULL,
  `rank_today` smallint DEFAULT NULL,
  `confidence_tier` enum('A','B','C','D') DEFAULT NULL,
  `picked` tinyint(1) DEFAULT '0',
  `model_version` varchar(20) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_date_rank` (`game_date`,`rank_today`),
  KEY `idx_date_pick` (`game_date`,`picked`),
  KEY `idx_hitter_date` (`hitter_id`,`game_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table recommendations
# ------------------------------------------------------------

CREATE TABLE `recommendations` (
  `id` int NOT NULL AUTO_INCREMENT,
  `game_pk` int DEFAULT NULL,
  `game_date` date DEFAULT NULL,
  `hitter_id` int DEFAULT NULL,
  `hitter_name` varchar(255) DEFAULT NULL,
  `team_abbr` varchar(10) DEFAULT NULL,
  `opp_pitcher_name` varchar(255) DEFAULT NULL,
  `p_at_least_1` float DEFAULT NULL,
  `tier` varchar(1) DEFAULT NULL,
  `rank` int DEFAULT NULL,
  `has_hit` tinyint(1) DEFAULT NULL,
  `actual_hits` int DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `game_date` (`game_date`,`hitter_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table results
# ------------------------------------------------------------

CREATE TABLE `results` (
  `id` int NOT NULL AUTO_INCREMENT,
  `game_date` date NOT NULL,
  `game_pk` int NOT NULL,
  `hitter_id` int NOT NULL,
  `hitter_name` varchar(80) NOT NULL,
  `ab` tinyint DEFAULT NULL,
  `hits` tinyint DEFAULT NULL,
  `got_hit` tinyint(1) DEFAULT NULL,
  `prediction_id` int DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_date` (`game_date`),
  KEY `idx_pred` (`prediction_id`),
  CONSTRAINT `results_ibfk_1` FOREIGN KEY (`prediction_id`) REFERENCES `predictions` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;



# Dump of table streak_log
# ------------------------------------------------------------

CREATE TABLE `streak_log` (
  `id` int NOT NULL AUTO_INCREMENT,
  `game_date` date NOT NULL,
  `pick_number` tinyint NOT NULL DEFAULT '1',
  `hitter_id` int NOT NULL,
  `hitter_name` varchar(80) NOT NULL,
  `prediction_id` int DEFAULT NULL,
  `got_hit` tinyint(1) DEFAULT NULL,
  `streak_before` int NOT NULL DEFAULT '0',
  `streak_after` int DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_date` (`game_date`),
  KEY `prediction_id` (`prediction_id`),
  CONSTRAINT `streak_log_ibfk_1` FOREIGN KEY (`prediction_id`) REFERENCES `predictions` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;




/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;
/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
