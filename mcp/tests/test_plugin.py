import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugin"
SKILL_FILES = {
    "mnemonic-save": PLUGIN_ROOT / "skills" / "mnemonic-save" / "SKILL.md",
    "mnemonic-search": PLUGIN_ROOT / "skills" / "mnemonic-search" / "SKILL.md",
    "mnemonic-recall": PLUGIN_ROOT / "skills" / "mnemonic-recall" / "SKILL.md",
}
REFERENCE_FILES = {
    "authority-and-provenance.md",
    "repository-freshness.md",
    "work-graph.md",
}
BIN_FILES = {"mnemonic-repository-freshness"}


def test_plugin_manifest_and_inventory_are_exact():
    inner = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads(
        (REPOSITORY_ROOT / ".claude-plugin" / "marketplace.json").read_text()
    )

    assert inner["name"] == "mnemonic"
    assert inner["version"] == "0.9.0"
    assert "duplicate merges" in inner["description"]
    assert "declared repository scope" in inner["description"]
    assert marketplace["plugins"] == [
        {
            "name": "mnemonic",
            "source": "./plugin",
            "description": inner["description"],
        }
    ]
    assert {path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")} == (
        set(SKILL_FILES)
    )
    assert {path.name for path in (PLUGIN_ROOT / "reference").glob("*.md")} == (
        REFERENCE_FILES
    )
    assert {path.name for path in (PLUGIN_ROOT / "bin").iterdir()} == BIN_FILES
    helper = PLUGIN_ROOT / "bin" / "mnemonic-repository-freshness"
    assert helper.stat().st_mode & 0o111 == 0o111


def test_every_skill_agrees_on_gate_authority_and_dual_graph_facts():
    for name, path in SKILL_FILES.items():
        content = path.read_text()
        lowered = content.lower()
        assert "request_human_input" in content, name
        assert "authority-and-provenance.md" in content, name
        assert "parent-child" in content, name
        assert "discovered-from" in content, name
        assert "never infer" in lowered, name
        assert "dashboard" in lowered, name
        assert "execution authority" in lowered or "automatic execution" in lowered, name
        assert "resolve_human_input" not in content, name

        for relative in re.findall(
            r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)", content
        ):
            assert (PLUGIN_ROOT / relative).is_file(), (name, relative)


def test_shared_references_freeze_eleven_writes_and_permanent_merge():
    authority = (PLUGIN_ROOT / "reference" / "authority-and-provenance.md").read_text()
    graph = (PLUGIN_ROOT / "reference" / "work-graph.md").read_text()

    assert "These eleven canonical mutations" in authority
    assert "`merge_work`" in authority
    assert "permanent" in authority
    assert "typed `503 duplicate_graph_invalid`" in authority
    assert "not an unknown" in authority
    assert "do not retry" in authority
    assert "duplicate-handling aggregate audit" in authority
    assert "No canonical MCP tool resolves a gate" in authority
    assert "request_human_input" in authority
    assert "list_work_gates" in authority
    assert "No longer needed" in authority
    assert "cannot withdraw" in authority
    assert "`requested_context_revision`" in authority
    assert "backend-computed drift flags" in authority
    assert "resolve_human_input" not in authority
    assert "unresolved human gate" in graph
    assert "Only `parent-child` defines" in graph
    assert "record both facts atomically" in graph
    assert "Duplicate marks are not authoritative merges" in graph
    assert "sole authoritative operation" in graph
    assert "never replace" in graph.lower()
    assert "Fresh generic duplicate-of" in graph


def test_advisory_guidance_preserves_create_anyway_and_categorical_evidence():
    save = SKILL_FILES["mnemonic-save"].read_text()
    authority = (PLUGIN_ROOT / "reference" / "authority-and-provenance.md").read_text()

    assert "suggest_duplicate_work" in save
    assert "exact_title" in save
    assert "lexical" in save
    assert "semantic" in save
    assert re.search(r"create\s+anyway", save, re.IGNORECASE)
    assert "suggest_duplicate_work" in authority
    assert "evidence-only" in authority


def test_repository_freshness_guidance_uses_only_the_packaged_fixed_helper():
    recall = SKILL_FILES["mnemonic-recall"].read_text()
    freshness = (PLUGIN_ROOT / "reference" / "repository-freshness.md").read_text()
    executable = '"${CLAUDE_PLUGIN_ROOT}/bin/mnemonic-repository-freshness"'

    for content in (recall, freshness):
        assert executable in content
        assert "--baseline" in content
        assert "--path" in content
    assert "explicitly select the workspace" in freshness
    assert "15-second" in freshness
    assert "unchanged" in freshness
    assert "changed" in freshness
    assert "indeterminate" in freshness
    assert "semantic freshness" in freshness
    assert "correctness" in freshness
    assert "--repo" not in recall
    assert "--root" not in recall
