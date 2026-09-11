-- Migration: Add pipeline_logs table for real-time pipeline activity feed
-- Run this once on your MySQL server: mysql -h 187.127.163.17 -u <user> -p <dbname> < add_pipeline_logs.sql

CREATE TABLE IF NOT EXISTS `pipeline_logs` (
  `id`          INT(11)       NOT NULL AUTO_INCREMENT,
  `story_id`    INT(11)       NOT NULL,
  `workflow_id` INT(11)       DEFAULT NULL,
  `agent`       VARCHAR(100)  NOT NULL DEFAULT 'Orchestrator',
  `level`       VARCHAR(20)   NOT NULL DEFAULT 'info',
  `message`     TEXT          NOT NULL,
  `detail`      TEXT          DEFAULT NULL,
  `created_at`  DATETIME      DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_pipeline_logs_story_id` (`story_id`),
  KEY `idx_pipeline_logs_workflow_id` (`workflow_id`),
  CONSTRAINT `fk_pipeline_logs_story`    FOREIGN KEY (`story_id`)    REFERENCES `stories`   (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_pipeline_logs_workflow` FOREIGN KEY (`workflow_id`) REFERENCES `workflows` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SELECT 'pipeline_logs table created successfully.' AS status;
