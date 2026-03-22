"""Tests for agentfile.commands.migrate — schema migration."""

from __future__ import annotations


import yaml

from agentfile.commands.migrate import (
    _detect_version,
    _migrate,
    _migrate_1_0_to_1_1,
)


class TestDetectVersion:
    def test_schema_version(self):
        assert _detect_version({"schema_version": "1.1"}) == "1.1"

    def test_version_alias(self):
        assert _detect_version({"version": "1.0"}) == "1.0"

    def test_no_version_defaults_1_0(self):
        assert _detect_version({}) == "1.0"

    def test_schema_version_takes_precedence(self):
        assert _detect_version({"schema_version": "1.1", "version": "1.0"}) == "1.1"


class TestMigrate1_0To1_1:
    def test_renames_workspace_id(self):
        data = {
            "mcp_gateway": {"url": "http://gw", "workspace_id": "ws-123"},
        }
        migrated, changes = _migrate_1_0_to_1_1(data)
        assert "org_id" in migrated["mcp_gateway"]
        assert "workspace_id" not in migrated["mcp_gateway"]
        assert migrated["mcp_gateway"]["org_id"] == "ws-123"
        assert any("workspace_id" in c for c in changes)

    def test_sets_schema_version(self):
        data = {}
        migrated, changes = _migrate_1_0_to_1_1(data)
        assert migrated["schema_version"] == "1.1"
        assert any("schema_version" in c for c in changes)

    def test_no_gateway_still_works(self):
        data = {"agents": {}}
        migrated, changes = _migrate_1_0_to_1_1(data)
        assert migrated["schema_version"] == "1.1"


class TestMigrate:
    def test_full_migration_from_1_0(self):
        data = {
            "version": "1.0",
            "mcp_gateway": {"url": "http://gw", "workspace_id": "old"},
        }
        migrated, changes = _migrate(data)
        assert migrated["schema_version"] == "1.1"
        assert migrated["mcp_gateway"]["org_id"] == "old"
        assert len(changes) > 0

    def test_already_latest_no_changes(self):
        data = {"schema_version": "1.1"}
        migrated, changes = _migrate(data)
        assert changes == []

    def test_no_version_treated_as_1_0(self):
        data = {"agents": {}}
        migrated, changes = _migrate(data)
        assert migrated["schema_version"] == "1.1"


class TestMigrateCLI:
    def test_already_latest(self, tmp_path):
        """CLI should print 'nothing to do' and not modify the file."""
        from click.testing import CliRunner
        from agentfile.commands.migrate import migrate_cmd

        p = tmp_path / "agentfile.yaml"
        p.write_text(yaml.dump({"schema_version": "1.1", "agents": {}}))

        runner = CliRunner()
        result = runner.invoke(migrate_cmd, ["--file", str(p)])
        assert result.exit_code == 0
        assert "nothing to do" in result.output

    def test_dry_run(self, tmp_path):
        from click.testing import CliRunner
        from agentfile.commands.migrate import migrate_cmd

        p = tmp_path / "agentfile.yaml"
        original = yaml.dump({"version": "1.0", "agents": {}})
        p.write_text(original)

        runner = CliRunner()
        result = runner.invoke(migrate_cmd, ["--file", str(p), "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in result.output
        # File should NOT be modified
        assert p.read_text() == original

    def test_actual_migration(self, tmp_path):
        from click.testing import CliRunner
        from agentfile.commands.migrate import migrate_cmd

        p = tmp_path / "agentfile.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "agents": {"a": {"metadata": {}, "runtime": {}, "tools": []}},
            "mcp_gateway": {"url": "http://gw", "workspace_id": "old"},
        }))

        runner = CliRunner()
        result = runner.invoke(migrate_cmd, ["--file", str(p)])
        assert result.exit_code == 0

        migrated = yaml.safe_load(p.read_text())
        assert migrated["schema_version"] == "1.1"
        assert "org_id" in migrated["mcp_gateway"]

    def test_file_not_found(self, tmp_path):
        from click.testing import CliRunner
        from agentfile.commands.migrate import migrate_cmd

        runner = CliRunner()
        result = runner.invoke(migrate_cmd, ["--file", str(tmp_path / "missing.yaml")])
        assert result.exit_code != 0
