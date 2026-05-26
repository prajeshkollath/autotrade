-- Cross-agent shared memory — readable and writable by all Claude Code agents and Hermes
CREATE TABLE IF NOT EXISTS agent_memory (
    id           SERIAL PRIMARY KEY,
    key          TEXT UNIQUE NOT NULL,
    value        TEXT NOT NULL,
    source_agent TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    expires_at   TIMESTAMPTZ
);

-- Auto-update updated_at on every write
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER agent_memory_updated_at
    BEFORE UPDATE ON agent_memory
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Index for fast key lookup and agent filtering
CREATE INDEX IF NOT EXISTS idx_agent_memory_key ON agent_memory(key);
CREATE INDEX IF NOT EXISTS idx_agent_memory_source ON agent_memory(source_agent);
CREATE INDEX IF NOT EXISTS idx_agent_memory_expires ON agent_memory(expires_at) WHERE expires_at IS NOT NULL;
