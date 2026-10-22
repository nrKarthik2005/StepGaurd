-- StepGuard persistent records. Every episode can be traced back to its experiment,
-- configuration, workflow specification version (SHA-256), scenario and seed.

CREATE TABLE IF NOT EXISTS workflows (
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    family      TEXT NOT NULL,
    yaml        TEXT NOT NULL,
    loaded_at   TEXT NOT NULL,
    PRIMARY KEY (name, sha256)
);

CREATE TABLE IF NOT EXISTS experiments (
    id                 TEXT PRIMARY KEY,
    mode               TEXT NOT NULL,
    status             TEXT NOT NULL,               -- running | completed | failed
    created_at         TEXT NOT NULL,
    finished_at        TEXT,
    config_json        TEXT NOT NULL,
    spec_hashes_json   TEXT NOT NULL,
    n_planned          INTEGER NOT NULL,
    n_episodes         INTEGER DEFAULT 0,
    analysis           TEXT,
    error              TEXT,
    summary_json       TEXT
);

CREATE TABLE IF NOT EXISTS scenarios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id  TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    workflow       TEXT NOT NULL,
    template       INTEGER NOT NULL,
    seed           INTEGER NOT NULL,
    condition      TEXT NOT NULL,
    UNIQUE (experiment_id, workflow, template, seed, condition)
);

CREATE TABLE IF NOT EXISTS episodes (
    id                      TEXT PRIMARY KEY,
    experiment_id           TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    scenario_id             INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    configuration           TEXT NOT NULL,
    completed               INTEGER NOT NULL,
    escalated               INTEGER NOT NULL,
    escalation_reason       TEXT,
    fault_fired             INTEGER NOT NULL,
    fault_variant           TEXT,
    fault_step              INTEGER,
    detected                INTEGER NOT NULL,
    executed                INTEGER NOT NULL,
    unsafe_executed         INTEGER NOT NULL,
    unsafe_proposed         INTEGER NOT NULL,
    unsafe_blocked          INTEGER NOT NULL,
    correct_steps           INTEGER NOT NULL,
    false_flags             INTEGER NOT NULL,
    proposals               INTEGER NOT NULL,
    blocks                  INTEGER NOT NULL,
    retries                 INTEGER NOT NULL,
    tool_calls              INTEGER NOT NULL,
    state_reads             INTEGER NOT NULL,
    duplicate_side_effects  INTEGER NOT NULL,
    virtual_time            REAL NOT NULL,
    verification_ms         REAL NOT NULL,
    recovery_latency        REAL,
    final_stage             TEXT
);
CREATE INDEX IF NOT EXISTS idx_episodes_exp ON episodes(experiment_id, configuration);

CREATE TABLE IF NOT EXISTS step_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id            TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    step                  INTEGER NOT NULL,
    action                TEXT NOT NULL,
    args_json             TEXT,
    origin                TEXT,
    stage_before          TEXT,
    expected_stage        TEXT,
    observed_stage        TEXT,
    verdict               TEXT NOT NULL,
    failed_predicate      TEXT,
    message               TEXT,
    output_json           TEXT,
    error                 TEXT,
    fault                 TEXT,
    probe                 TEXT,
    recovery              TEXT,
    recovery_reason       TEXT,
    checkpoint            INTEGER,
    barrier               INTEGER,
    in_flight_json        TEXT,
    restoration_verified  INTEGER,
    executed              INTEGER,
    oracle_unsafe         INTEGER,
    timestamp             REAL,
    verification_ms       REAL,
    virtual_time          REAL
);
CREATE INDEX IF NOT EXISTS idx_steps_episode ON step_events(episode_id);

CREATE TABLE IF NOT EXISTS injected_faults (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id  TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    fault_type  TEXT NOT NULL,
    variant     TEXT,
    step        INTEGER
);

CREATE TABLE IF NOT EXISTS checkpoint_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id   TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    step         INTEGER NOT NULL,
    event        TEXT NOT NULL,
    idx          INTEGER NOT NULL,
    stage        TEXT,
    history_len  INTEGER,
    barrier      INTEGER,
    verified     INTEGER
);

CREATE TABLE IF NOT EXISTS recovery_decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id       TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    step             INTEGER NOT NULL,
    action           TEXT NOT NULL,
    verdict          TEXT NOT NULL,
    probe            TEXT,
    strategy         TEXT NOT NULL,
    reason           TEXT,
    admissible_json  TEXT
);

CREATE TABLE IF NOT EXISTS metric_summaries (
    experiment_id  TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    configuration  TEXT NOT NULL,
    metric         TEXT NOT NULL,
    value          REAL,
    PRIMARY KEY (experiment_id, configuration, metric)
);

CREATE TABLE IF NOT EXISTS simulations (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    workflow      TEXT NOT NULL,
    workflow_sha  TEXT NOT NULL,
    configuration TEXT NOT NULL,
    fault         TEXT,
    template      INTEGER NOT NULL,
    seed          INTEGER NOT NULL,
    completed     INTEGER NOT NULL,
    result_json   TEXT NOT NULL
);
