"""PM-1 durable planning foundation. PostgreSQL 15+ column-specific SET NULL.

Frozen DDL: independent of future application model changes.
"""
from alembic import op

revision = "0021_planning_foundation"
down_revision = "0020_user_knowledge_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
ALTER TABLE conversation_turns ADD CONSTRAINT uq_conversation_turns_id_session_user UNIQUE (id, session_id, user_id)
"""
    )
    op.execute(
        """
CREATE TABLE plans (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	goal TEXT, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	deadline_at TIMESTAMP WITH TIME ZONE, 
	timezone VARCHAR(64) DEFAULT 'UTC' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	source_session_id UUID, 
	source_turn_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(source_session_id, user_id) REFERENCES voice_sessions (id, user_id) ON DELETE SET NULL (source_session_id), 
	FOREIGN KEY(source_turn_id, user_id) REFERENCES conversation_turns (id, user_id) ON DELETE SET NULL (source_turn_id), 
	CONSTRAINT uq_plans_id_user_id UNIQUE (id, user_id), 
	CONSTRAINT ck_plans_status CHECK (status IN ('active', 'completed', 'archived')), 
	CONSTRAINT ck_plans_revision CHECK (revision > 0)
)
"""
    )
    op.execute('CREATE INDEX ix_plans_user_created ON plans (user_id, created_at)')
    op.execute(
        """
CREATE TABLE plan_context_items (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	plan_id UUID NOT NULL, 
	kind VARCHAR(32) DEFAULT 'note' NOT NULL, 
	content TEXT NOT NULL, 
	value_json JSONB, 
	source_turn_id UUID, 
	source_span_json JSONB, 
	dedupe_key VARCHAR(64) NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(plan_id, user_id) REFERENCES plans (id, user_id) ON DELETE CASCADE, 
	FOREIGN KEY(source_turn_id, user_id) REFERENCES conversation_turns (id, user_id) ON DELETE SET NULL (source_turn_id), 
	CONSTRAINT uq_plan_context_id_user UNIQUE (id, user_id), 
	CONSTRAINT uq_plan_context_dedupe UNIQUE (plan_id, user_id, dedupe_key), 
	CONSTRAINT ck_plan_context_revision CHECK (revision > 0), 
	CONSTRAINT ck_plan_context_status CHECK (status IN ('active', 'deleted'))
)
"""
    )
    op.execute(
        """
CREATE TABLE planning_sessions (
	session_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	mode VARCHAR(16) DEFAULT 'normal' NOT NULL, 
	active_plan_id UUID, 
	state_version INTEGER DEFAULT '1' NOT NULL, 
	policy_version VARCHAR(64) DEFAULT 'plan-v1' NOT NULL, 
	enabled_at TIMESTAMP WITH TIME ZONE, 
	disabled_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (session_id), 
	FOREIGN KEY(session_id, user_id) REFERENCES voice_sessions (id, user_id) ON DELETE CASCADE, 
	FOREIGN KEY(active_plan_id, user_id) REFERENCES plans (id, user_id) ON DELETE SET NULL (active_plan_id), 
	CONSTRAINT uq_planning_sessions_session_user UNIQUE (session_id, user_id), 
	CONSTRAINT ck_planning_sessions_mode CHECK (mode IN ('normal', 'plan')), 
	CONSTRAINT ck_planning_sessions_version CHECK (state_version > 0)
)
"""
    )
    op.execute(
        """
CREATE TABLE planning_batches (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	session_id UUID NOT NULL, 
	turn_id UUID NOT NULL, 
	state_version INTEGER NOT NULL, 
	extractor_version VARCHAR(64) NOT NULL, 
	policy_version VARCHAR(64) NOT NULL, 
	source_digest VARCHAR(64) NOT NULL, 
	status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	retry_count INTEGER DEFAULT '0' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id, user_id) REFERENCES planning_sessions (session_id, user_id) ON DELETE CASCADE, 
	FOREIGN KEY(turn_id, session_id, user_id) REFERENCES conversation_turns (id, session_id, user_id) ON DELETE CASCADE, 
	CONSTRAINT uq_planning_batches_id_user UNIQUE (id, user_id), 
	CONSTRAINT uq_planning_batches_turn_user UNIQUE (turn_id, user_id), 
	CONSTRAINT ck_planning_batches_status CHECK (status IN ('pending', 'validated', 'completed', 'failed', 'cancelled')), 
	CONSTRAINT ck_planning_batches_versions CHECK (state_version > 0 AND retry_count >= 0)
)
"""
    )
    op.execute(
        """
CREATE TABLE planning_actions (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	batch_id UUID NOT NULL, 
	plan_id UUID, 
	ordinal INTEGER NOT NULL, 
	action_type VARCHAR(64) NOT NULL, 
	payload_json JSONB NOT NULL, 
	payload_digest VARCHAR(64) NOT NULL, 
	source_spans_json JSONB NOT NULL, 
	disposition VARCHAR(16) NOT NULL, 
	reason VARCHAR(128), 
	target_id UUID, 
	target_revision INTEGER, 
	confidence FLOAT DEFAULT '1' NOT NULL, 
	committed_at TIMESTAMP WITH TIME ZONE, 
	tool_call_id VARCHAR(128), 
	status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	result_json JSONB, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(batch_id, user_id) REFERENCES planning_batches (id, user_id) ON DELETE CASCADE, 
	FOREIGN KEY(plan_id, user_id) REFERENCES plans (id, user_id) ON DELETE SET NULL (plan_id), 
	CONSTRAINT uq_planning_actions_id_user UNIQUE (id, user_id), 
	CONSTRAINT uq_planning_actions_ordinal UNIQUE (batch_id, ordinal), 
	CONSTRAINT ck_planning_actions_ordinal CHECK (ordinal >= 0), 
	CONSTRAINT ck_planning_actions_confidence CHECK (confidence >= 0 AND confidence <= 1), 
	CONSTRAINT ck_planning_actions_target_revision CHECK (target_revision IS NULL OR target_revision > 0), 
	CONSTRAINT ck_planning_actions_disposition CHECK (disposition IN ('AUTO', 'CONFIRM', 'CLARIFY', 'NO_ACTION', 'DENY')), 
	CONSTRAINT ck_planning_actions_status CHECK (status IN ('pending', 'completed', 'failed', 'cancelled', 'duplicate', 'skipped'))
)
"""
    )
    op.execute('CREATE INDEX ix_planning_actions_user_plan_created ON planning_actions (user_id, plan_id, created_at)')
    op.execute(
        """
ALTER TABLE tasks ADD COLUMN plan_id UUID NULL, ADD COLUMN planning_action_id UUID NULL, ADD COLUMN revision INTEGER NOT NULL DEFAULT 1
"""
    )
    op.execute(
        """
ALTER TABLE tasks ADD CONSTRAINT fk_tasks_planning_action_user FOREIGN KEY(planning_action_id, user_id) REFERENCES planning_actions (id, user_id) ON DELETE SET NULL (planning_action_id)
"""
    )
    op.execute('ALTER TABLE tasks ADD CONSTRAINT ck_tasks_revision CHECK (revision > 0)')
    op.execute(
        """
ALTER TABLE tasks ADD CONSTRAINT fk_tasks_plan_user FOREIGN KEY(plan_id, user_id) REFERENCES plans (id, user_id) ON DELETE SET NULL (plan_id)
"""
    )
    op.execute("CREATE INDEX ix_tasks_user_plan ON tasks (user_id, plan_id)")
    op.execute(
        """
ALTER TABLE reminders ADD COLUMN plan_id UUID NULL, ADD COLUMN planning_action_id UUID NULL, ADD COLUMN revision INTEGER NOT NULL DEFAULT 1
"""
    )
    op.execute(
        """
ALTER TABLE reminders ADD CONSTRAINT fk_reminders_planning_action_user FOREIGN KEY(planning_action_id, user_id) REFERENCES planning_actions (id, user_id) ON DELETE SET NULL (planning_action_id)
"""
    )
    op.execute('ALTER TABLE reminders ADD CONSTRAINT ck_reminders_revision CHECK (revision > 0)')
    op.execute(
        """
ALTER TABLE reminders ADD CONSTRAINT fk_reminders_plan_user FOREIGN KEY(plan_id, user_id) REFERENCES plans (id, user_id) ON DELETE SET NULL (plan_id)
"""
    )
    op.execute("CREATE INDEX ix_reminders_user_plan ON reminders (user_id, plan_id)")
    op.execute("ALTER TABLE reminders ADD COLUMN source_turn_id UUID NULL")
    op.execute(
        """
ALTER TABLE reminders ADD CONSTRAINT fk_reminders_source_turn_user FOREIGN KEY (source_turn_id, user_id) REFERENCES conversation_turns (id, user_id) ON DELETE SET NULL (source_turn_id)
"""
    )

    op.execute(
        """
ALTER TABLE tasks DROP CONSTRAINT fk_tasks_source_turn_user, ADD CONSTRAINT fk_tasks_source_turn_user FOREIGN KEY (source_turn_id, user_id) REFERENCES conversation_turns (id, user_id) ON DELETE SET NULL (source_turn_id)
"""
    )
    op.execute(
        """
ALTER TABLE reminders DROP CONSTRAINT fk_reminders_task_user, ADD CONSTRAINT fk_reminders_task_user FOREIGN KEY (task_id, user_id) REFERENCES tasks (id, user_id) ON DELETE SET NULL (task_id)
"""
    )


def downgrade() -> None:
    op.execute(
        """
ALTER TABLE reminders DROP CONSTRAINT fk_reminders_task_user, ADD CONSTRAINT fk_reminders_task_user FOREIGN KEY (task_id, user_id) REFERENCES tasks (id, user_id) ON DELETE SET NULL
"""
    )
    op.execute(
        """
ALTER TABLE tasks DROP CONSTRAINT fk_tasks_source_turn_user, ADD CONSTRAINT fk_tasks_source_turn_user FOREIGN KEY (source_turn_id, user_id) REFERENCES conversation_turns (id, user_id) ON DELETE SET NULL
"""
    )
    op.execute("ALTER TABLE reminders DROP CONSTRAINT fk_reminders_source_turn_user, DROP COLUMN source_turn_id")
    op.execute("ALTER TABLE reminders DROP COLUMN planning_action_id, DROP COLUMN plan_id, DROP COLUMN revision")
    op.execute("ALTER TABLE tasks DROP COLUMN planning_action_id, DROP COLUMN plan_id, DROP COLUMN revision")
    op.drop_table("planning_actions")
    op.drop_table("planning_batches")
    op.drop_table("planning_sessions")
    op.drop_table("plan_context_items")
    op.drop_table("plans")
    op.execute("ALTER TABLE conversation_turns DROP CONSTRAINT uq_conversation_turns_id_session_user")
