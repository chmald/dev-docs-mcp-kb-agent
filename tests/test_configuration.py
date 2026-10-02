"""Configuration consistency guards -- offline, no Azure calls.

The pattern has three deployment paths (azd, deploy.ps1, manual) and one
configuration reference (docs/12). These tests fail when they drift apart:
a Bicep parameter that azd can't set, an azd variable nobody documented, a
script key missing from the reference, or a hook reading an output that the
template never produces.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"
DOC = (ROOT / "docs" / "12-configuration-reference.md").read_text(encoding="utf-8")

# Set by azd itself or consumed only for verification -- documented, never Bicep outputs.
AZD_PROVIDED = {"AZURE_ENV_NAME", "AZURE_SUBSCRIPTION_ID", "AZURE_LOCATION", "AZURE_TENANT_ID",
                "AZURE_PRINCIPAL_ID", "AZURE_PRINCIPAL_TYPE", "AZURE_RESOURCE_GROUP"}


def bicep_params(path: Path) -> set[str]:
    return set(re.findall(r"^param\s+(\w+)\s", path.read_text(encoding="utf-8"), re.M))


def bicep_outputs(path: Path) -> set[str]:
    return set(re.findall(r"^output\s+(\w+)\s", path.read_text(encoding="utf-8"), re.M))


def documented(name: str) -> bool:
    return f"`{name}`" in DOC or f"`{name} " in DOC or f"`{name}/" in DOC or f"`{name}." in DOC or f"/ `{name}" in DOC


def test_azd_parameters_file_matches_azd_bicep():
    params = json.loads((INFRA / "azd.parameters.json").read_text(encoding="utf-8"))["parameters"]
    assert set(params) == bicep_params(INFRA / "azd.bicep")


def test_azd_parameter_values_are_quoted_substitutions():
    """azd parses the file as JSON BEFORE substituting, so every value -- even a
    bool or int -- must be a quoted "${VAR}" / "${VAR=default}" string."""
    params = json.loads((INFRA / "azd.parameters.json").read_text(encoding="utf-8"))["parameters"]
    for name, spec in params.items():
        assert isinstance(spec["value"], str) and re.fullmatch(r"\$\{[A-Z_]+(=[^}]*)?\}", spec["value"]), name


def test_every_main_bicep_parameter_is_settable_through_azd():
    text = (INFRA / "azd.bicep").read_text(encoding="utf-8")
    module = text[text.index("module main 'main.bicep'"):]
    module = module[: module.index("\n}\n")]
    passed = set(re.findall(r"^\s{4}(\w+):", module, re.M))
    assert bicep_params(INFRA / "main.bicep") <= passed


def test_every_azd_variable_is_documented():
    raw = (INFRA / "azd.parameters.json").read_text(encoding="utf-8")
    for var in re.findall(r"\$\{([A-Z_]+)", raw):
        assert documented(var), f"azd variable {var} is missing from docs/12"


def test_every_bicep_output_is_documented_and_read_by_the_hook():
    outputs = bicep_outputs(INFRA / "azd.bicep")
    hook = (INFRA / "hooks" / "postprovision.ps1").read_text(encoding="utf-8")
    read = set(re.findall(r"Get-Out '([A-Z_]+)'", hook))
    for out in outputs:
        assert documented(out), f"output {out} is missing from docs/12"
    unknown = read - outputs - AZD_PROVIDED - {"DEMO_CORPUS_DIR", "DEMO_PYTHON", "DEMO_SPLIT"}
    assert not unknown, f"postprovision reads values nothing produces: {unknown}"


def test_hook_knobs_are_documented():
    for hook in (INFRA / "hooks").glob("*.ps1"):
        text = hook.read_text(encoding="utf-8")
        for var in set(re.findall(r"\$env:([A-Z_]+)", text)) | set(re.findall(r"Get-Out '((?:DEMO|WORKLOAD)_[A-Z_]+)'", text)):
            assert documented(var), f"{hook.name} reads {var}, which docs/12 does not document"


def test_ids_written_by_deployment_exist_in_the_bicep_summary():
    common = (INFRA / "hooks" / "common.ps1").read_text(encoding="utf-8")
    block = common[common.index("$keys = @("): common.index(")", common.index("$keys = @("))]
    keys = set(re.findall(r"'(\w+)'", block))
    main = (INFRA / "main.bicep").read_text(encoding="utf-8")
    summary = main[main.index("output deploymentSummary"):]
    summary_keys = set(re.findall(r"^\s{2}(\w+):", summary, re.M))
    assert keys <= summary_keys, keys - summary_keys


def test_every_script_setting_is_documented():
    ids_key = re.compile(r"""ids(?:\.get\(|\[)["'](\w+)["']""")
    env_key = re.compile(r"""os\.environ(?:\.get\(|\[)["'](\w+)["']""")
    missing = []
    for py in (ROOT / "scripts").glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for key in set(ids_key.findall(text)) | set(env_key.findall(text)):
            if key in {"corpus"}:
                continue
            if not documented(key):
                missing.append(f"{py.name}: {key}")
    assert not missing, "undocumented settings in docs/12: " + ", ".join(sorted(missing))


def test_azure_yaml_points_at_the_azd_module_and_hooks_exist():
    yaml = (ROOT / "azure.yaml").read_text(encoding="utf-8")
    assert re.search(r"^\s+module:\s+azd\s*$", yaml, re.M)
    for script in re.findall(r"run:\s+\./(\S+)", yaml):
        assert (ROOT / script).exists(), script


def test_azd_state_is_gitignored():
    assert re.search(r"^\.azure/\s*$", (ROOT / ".gitignore").read_text(encoding="utf-8"), re.M)
