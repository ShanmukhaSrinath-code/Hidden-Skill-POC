-- Phase 2: agent pipeline state and the employee skill graph (needs canonical_schema.sql). Safe to re-run.
-- Graph shape: EMPLOYEE -HAS_SKILL-> SKILL -SUPPORTED_BY-> EVIDENCE -> source message (OUTCOME / validation).

-- Skill taxonomy (seeded by integrations/skills.py; new skills found by Agent 2 are added).
CREATE TABLE IF NOT EXISTS skills (
    skill_id   TEXT PRIMARY KEY,               -- slug, e.g. redis-caching
    name       TEXT NOT NULL UNIQUE,
    category   TEXT NOT NULL
);

-- One row per thread per pipeline run: what each agent decided (audit trail, and skip unchanged threads).
CREATE TABLE IF NOT EXISTS agent_thread_runs (
    thread_key      TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL,
    status          TEXT NOT NULL,              -- filtered_out | processed | no_interactions | error
    agent1          JSONB,                      -- relevance filter
    agent2          JSONB,                      -- interactions: helper, requester, skill, topic
    agent3          JSONB,                      -- validation per interaction
    models          JSONB,
    error           TEXT,
    processed_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Validated evidence: one row per (employee, skill, helping message). Idempotent: replays add nothing twice.
CREATE TABLE IF NOT EXISTS skill_evidence (
    evidence_id        TEXT PRIMARY KEY,         -- ev_<hash>
    emp_id             TEXT NOT NULL REFERENCES employees(emp_id) ON DELETE CASCADE,
    skill_id           TEXT NOT NULL REFERENCES skills(skill_id),
    requester_emp_id   TEXT REFERENCES employees(emp_id) ON DELETE SET NULL,
    thread_key         TEXT NOT NULL,
    activity_id        TEXT NOT NULL,            -- the helping message (activity_records)
    validation         TEXT NOT NULL,            -- explicit | implicit
    score              INTEGER NOT NULL,         -- 10 for explicit; 1-10 implicit judgement
    signal             TEXT,                     -- what validated it ("That fixed it", "status -> Done", model reason)
    topic              TEXT,
    source             TEXT,
    source_url         TEXT,
    occurred_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (emp_id, skill_id, activity_id)
);
CREATE INDEX IF NOT EXISTS idx_skill_evidence_emp   ON skill_evidence (emp_id, skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_evidence_thread ON skill_evidence (thread_key);

-- The employee skill graph: explicit (declared) and discovered skills with evidence-backed strength.
CREATE TABLE IF NOT EXISTS employee_skills (
    emp_id             TEXT NOT NULL REFERENCES employees(emp_id) ON DELETE CASCADE,
    skill_id           TEXT NOT NULL REFERENCES skills(skill_id),
    kind               TEXT NOT NULL,            -- explicit (declared in HRMS/resume) | discovered (outside the profile)
    declared_source    TEXT,                     -- hrms / resume / hrms,resume
    evidence_count     INTEGER NOT NULL DEFAULT 0,
    explicit_count     INTEGER NOT NULL DEFAULT 0,
    implicit_avg       NUMERIC(4,1),
    requester_emp_ids  TEXT[] NOT NULL DEFAULT '{}',
    sources            TEXT[] NOT NULL DEFAULT '{}',
    confidence         NUMERIC(3,2),             -- NULL = declared only, no evidence yet
    first_seen         TIMESTAMPTZ,
    last_seen          TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (emp_id, skill_id)
);

-- Expert finder: who is strongest at each skill, ranked by evidence-backed confidence.
CREATE OR REPLACE VIEW skill_experts AS
SELECT s.skill_id, s.name AS skill, s.category, es.emp_id, e.full_name, e.role, es.kind,
       es.evidence_count, es.explicit_count, es.confidence, es.last_seen,
       rank() OVER (PARTITION BY s.skill_id ORDER BY es.confidence DESC NULLS LAST, es.evidence_count DESC) AS rank
FROM employee_skills es
JOIN skills s ON s.skill_id = es.skill_id
JOIN employees e ON e.emp_id = es.emp_id
WHERE es.evidence_count > 0;

-- Who people go to for help (mentorship signal): requester -> helper, with the skills involved.
CREATE OR REPLACE VIEW help_edges AS
SELECT ev.requester_emp_id, req.full_name AS requester, ev.emp_id AS helper_emp_id, hlp.full_name AS helper,
       count(*) AS times_helped, array_agg(DISTINCT s.name) AS skills, max(ev.occurred_at) AS last_helped
FROM skill_evidence ev
JOIN employees req ON req.emp_id = ev.requester_emp_id
JOIN employees hlp ON hlp.emp_id = ev.emp_id
JOIN skills s ON s.skill_id = ev.skill_id
WHERE ev.requester_emp_id IS NOT NULL AND ev.requester_emp_id <> ev.emp_id
GROUP BY ev.requester_emp_id, req.full_name, ev.emp_id, hlp.full_name;
