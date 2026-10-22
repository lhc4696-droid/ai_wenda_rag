-- Keep existing display names and folder capabilities; naming never renames disk paths.
ALTER TABLE local_coding_projects ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE local_coding_projects ADD COLUMN name_request TEXT NOT NULL DEFAULT '';
UPDATE local_coding_projects SET name=directory_name;
CREATE TABLE IF NOT EXISTS local_coding_project_numbers (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_number INTEGER NOT NULL DEFAULT 0 CHECK(last_number >= 0)
);
