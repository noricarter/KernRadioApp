"""Versioned human instructions; persistence only, with no agent or rule engine."""
from datetime import datetime, timezone
import re


class PolicyConflict(ValueError):
    pass


class PolicyNotFound(LookupError):
    pass


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS operating_policies (
        id INTEGER PRIMARY KEY,
        subject TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL CHECK(kind IN ('default', 'operational')),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
        created_at TEXT NOT NULL,
        modified_at TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS operating_policy_revisions (
        policy_id INTEGER NOT NULL REFERENCES operating_policies(id),
        revision INTEGER NOT NULL,
        title TEXT NOT NULL,
        instruction TEXT NOT NULL,
        rationale TEXT NOT NULL,
        basis TEXT NOT NULL,
        approval_status TEXT NOT NULL CHECK(approval_status IN ('proposed', 'approved', 'superseded')),
        declined INTEGER NOT NULL DEFAULT 0 CHECK(declined IN (0, 1)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(policy_id, revision)
    )''',
    "CREATE UNIQUE INDEX IF NOT EXISTS one_approved_policy_revision ON operating_policy_revisions(policy_id) WHERE approval_status = 'approved'",
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} must be nonempty text up to {maximum} characters.')


class OperatingPolicyService:
    def __init__(self, database):
        self._database = database
        with database.transaction() as session:
            for statement in _SCHEMA:
                session.execute(statement)
            columns = {column['name'] for column in session.fetch_all('PRAGMA table_info(operating_policy_revisions)')}
            if 'declined' not in columns:
                session.execute('ALTER TABLE operating_policy_revisions ADD COLUMN declined INTEGER NOT NULL DEFAULT 0 CHECK(declined IN (0, 1))')

    @staticmethod
    def _get(session, policy_id):
        policy = session.fetch_one('SELECT * FROM operating_policies WHERE id = ?', (policy_id,))
        if policy is None:
            return None
        latest = session.fetch_one('SELECT * FROM operating_policy_revisions WHERE policy_id = ? ORDER BY revision DESC LIMIT 1', (policy_id,))
        approved = session.fetch_one("SELECT * FROM operating_policy_revisions WHERE policy_id = ? AND approval_status = 'approved'", (policy_id,))
        policy.update(latest_revision=latest, approved_revision=approved,
                      active=bool(policy['enabled'] and approved))
        policy['enabled'] = bool(policy['enabled'])
        return policy

    def get_policy(self, policy_id):
        with self._database.transaction(write=False) as session:
            return self._get(session, policy_id)

    @staticmethod
    def _content(title, instruction, rationale, basis):
        for value, name, maximum in ((title, 'Title', 200), (instruction, 'Instruction', 4000),
                                      (rationale, 'Rationale', 2000), (basis, 'Basis', 2000)):
            _text(value, name, maximum)

    def create_proposal(self, subject, title, instruction, *, kind='default', rationale, basis):
        """Same subject/content is idempotent. Different content requires revise()."""
        if not isinstance(subject, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', subject) or len(subject) > 100:
            raise ValueError('Subject must be a stable lowercase hyphenated key up to 100 characters.')
        if kind not in ('default', 'operational'):
            raise ValueError('Only default preferences and operational requirements can be stored.')
        self._content(title, instruction, rationale, basis)
        with self._database.transaction() as session:
            existing = session.fetch_one('SELECT id FROM operating_policies WHERE subject = ?', (subject,))
            if existing:
                policy = self._get(session, existing['id'])
                latest = policy['latest_revision']
                if policy['kind'] == kind and all(latest[key] == value for key, value in
                    (('title', title), ('instruction', instruction), ('rationale', rationale), ('basis', basis))):
                    return policy
                raise PolicyConflict(f"Subject already exists as policy {policy['id']}; review and revise that policy instead.")
            now = _now()
            result = session.execute('INSERT INTO operating_policies(subject, kind, created_at, modified_at) VALUES (?, ?, ?, ?)', (subject, kind, now, now))
            session.execute('''INSERT INTO operating_policy_revisions
                (policy_id, revision, title, instruction, rationale, basis, approval_status, created_at)
                VALUES (?, 1, ?, ?, ?, ?, 'proposed', ?)''', (result.lastrowid, title, instruction, rationale, basis, now))
            return self._get(session, result.lastrowid)

    def revise(self, policy_id, expected_revision, *, title, instruction, rationale, basis):
        self._content(title, instruction, rationale, basis)
        with self._database.transaction() as session:
            policy = self._get(session, policy_id)
            if policy is None:
                raise PolicyNotFound('Policy does not exist.')
            latest = policy['latest_revision']
            if latest['revision'] != expected_revision:
                raise PolicyConflict('This policy changed. Review its latest revision first.')
            if not latest['declined'] and all(latest[key] == value for key, value in
                   (('title', title), ('instruction', instruction), ('rationale', rationale), ('basis', basis))):
                return policy
            now = _now()
            session.execute('''INSERT INTO operating_policy_revisions
                (policy_id, revision, title, instruction, rationale, basis, approval_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)''',
                (policy_id, expected_revision + 1, title, instruction, rationale, basis, now))
            session.execute('UPDATE operating_policies SET modified_at = ? WHERE id = ?', (now, policy_id))
            return self._get(session, policy_id)

    def approve(self, policy_id, expected_revision):
        """Trusted caller only: the application must obtain human approval first."""
        with self._database.transaction() as session:
            policy = self._get(session, policy_id)
            if policy is None:
                raise PolicyNotFound('Policy does not exist.')
            if policy['latest_revision']['revision'] != expected_revision:
                raise PolicyConflict('Approval refers to an outdated revision. Review the current text first.')
            if policy['latest_revision']['declined']:
                raise PolicyConflict('This proposal was declined. Revise it before approving it.')
            session.execute("UPDATE operating_policy_revisions SET approval_status = 'superseded' WHERE policy_id = ? AND approval_status = 'approved'", (policy_id,))
            session.execute("UPDATE operating_policy_revisions SET approval_status = 'approved' WHERE policy_id = ? AND revision = ?", (policy_id, expected_revision))
            session.execute('UPDATE operating_policies SET enabled = 1, modified_at = ? WHERE id = ?', (_now(), policy_id))
            return self._get(session, policy_id)

    def disable(self, policy_id, expected_revision):
        with self._database.transaction() as session:
            policy = self._get(session, policy_id)
            if policy is None:
                raise PolicyNotFound('Policy does not exist.')
            if policy['latest_revision']['revision'] != expected_revision:
                raise PolicyConflict('This policy changed. Review its latest revision before disabling it.')
            session.execute('UPDATE operating_policies SET enabled = 0, modified_at = ? WHERE id = ?', (_now(), policy_id))
            return self._get(session, policy_id)

    def decline(self, policy_id, expected_revision):
        """Decline this proposal without disabling any earlier approved revision."""
        with self._database.transaction() as session:
            policy = self._get(session, policy_id)
            if policy is None:
                raise PolicyNotFound('Policy does not exist.')
            latest = policy['latest_revision']
            if latest['revision'] != expected_revision or latest['approval_status'] != 'proposed':
                raise PolicyConflict('Only the current proposed revision can be declined.')
            session.execute('UPDATE operating_policy_revisions SET declined = 1 WHERE policy_id = ? AND revision = ?', (policy_id, expected_revision))
            session.execute('UPDATE operating_policies SET modified_at = ? WHERE id = ?', (_now(), policy_id))
            return self._get(session, policy_id)

    def list_policies(self, *, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('Invalid policy page.')
        with self._database.transaction(write=False) as session:
            rows = session.fetch_all('SELECT id FROM operating_policies ORDER BY id LIMIT ? OFFSET ?', (limit, offset))
            return [self._get(session, row['id']) for row in rows]

    def active_policies(self, *, kind=None):
        if kind not in (None, 'default', 'operational'):
            raise ValueError('Unknown policy kind.')
        where = ' AND p.kind = ?' if kind else ''
        return self._database.fetch_all(f'''SELECT p.id, p.subject, p.kind, p.created_at, p.modified_at,
            r.revision, r.title, r.instruction, r.rationale, r.basis, r.approval_status
            FROM operating_policies p JOIN operating_policy_revisions r ON r.policy_id = p.id
            WHERE p.enabled = 1 AND r.approval_status = 'approved' {where} ORDER BY p.id''', (kind,) if kind else ())
