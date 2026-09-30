"""Offline tests for the IDC reference-data pipeline scripts.

These exercise the pure, network-free logic: request validation, build-workflow
generation (+ gxformat2 validation), bundle-URL resolution from an invocation,
and the CVMFS import command assembly / record idempotency. No Galaxy or
toolshed access is required.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_data_exists as cde  # noqa: E402
import generate_build as gb  # noqa: E402
import generate_schema as gs  # noqa: E402
import get_bundle_urls as gburls  # noqa: E402
import import_bundles as imp  # noqa: E402
import request_models as rm  # noqa: E402
import tool_schemas as tsch  # noqa: E402

# Tool Shed parameter_request_schema responses, saved verbatim for every GUID the
# seed requests use (tests/fixtures/tool_schemas/<owner>~<repo>~<tool>~<version>.json).
FIXTURE_SCHEMAS = REPO_ROOT / "tests/fixtures/tool_schemas"


def fixture_schema(guid: str) -> dict:
    """A SchemaResolver over the saved Tool Shed responses - no network."""
    trs_id, version = tsch.trs_id_and_version(guid)
    path = FIXTURE_SCHEMAS / f"{trs_id}~{version}.json"
    if not path.is_file():
        raise tsch.SchemaUnavailable(f"no fixture for {guid}")
    return tsch.contributor_schema(json.loads(path.read_text()))

SEEDS = {
    "metaphlan": REPO_ROOT / "data-managers/metaphlan_database_versioned/mpa_vJan21_CHOCOPhlAnSGB_202103.yaml",
    "motus": REPO_ROOT / "data-managers/motus_db_versioned/3.1.0.yaml",
    "samestr": REPO_ROOT / "data-managers/samestr_db/marker_db_mpa_vJan21.yaml",
}


# --------------------------------------------------------------------------- #
# request_models
# --------------------------------------------------------------------------- #
def test_seed_requests_lint_clean():
    for path in SEEDS.values():
        assert rm.lint_file(path, fixture_schema) == [], path


def test_every_request_file_lints_clean_against_its_tool_schema():
    # Also covers request files added after the SEEDS dict was written.
    for path in rm.iter_request_files():
        assert rm.lint_file(path, fixture_schema) == [], path


def test_tool_id_must_be_version_pinned():
    with pytest.raises(Exception):
        rm.Request(tool_id="toolshed.g2.bx.psu.edu/repos/iuc/repo/tool", data_tables=["t"])
    with pytest.raises(Exception):
        rm.Request(tool_id="testtoolshed.g2.bx.psu.edu/repos/iuc/repo/tool/1.0", data_tables=["t"])
    # 6-part (version-pinned) is accepted
    rm.Request(tool_id="toolshed.g2.bx.psu.edu/repos/iuc/repo/tool/1.0", data_tables=["t"])


def test_request_rejects_unimplemented_checksum_field():
    with pytest.raises(Exception):
        rm.Request(
            tool_id="toolshed.g2.bx.psu.edu/repos/iuc/repo/tool/1.0",
            data_tables=["t"],
            checksum="sha256:abc",
        )


def test_lint_rejects_dir_table_mismatch(isolated_requests):
    p = isolated_requests / "motus_db_versioned" / "probe.yaml"
    p.parent.mkdir()
    p.write_text("tool_id: toolshed.g2.bx.psu.edu/repos/iuc/a/b/1\ndata_tables: [other]\n")
    errors = rm.lint_file(p)
    assert any("directory name" in e for e in errors)


# --------------------------------------------------------------------------- #
# tool_schemas / generate_schema: params are checked against the data manager
# --------------------------------------------------------------------------- #
MOTUS_GUID = "toolshed.g2.bx.psu.edu/repos/bgruening/data_manager_motus/motus_db_fetcher/3.1.0+galaxy2"
SAMESTR_GUID = "toolshed.g2.bx.psu.edu/repos/iuc/data_manager_samestr/samestr_db/1.2025.111+galaxy4"


@pytest.fixture
def isolated_requests(tmp_path, monkeypatch):
    """Point the linter at an empty data-managers/ tree so probe files never touch the real one."""
    root = tmp_path / "data-managers"
    root.mkdir()
    monkeypatch.setattr(rm, "DATA_MANAGERS_DIR", root)
    monkeypatch.setattr(rm, "REPO_ROOT", tmp_path)
    return root


def test_guid_maps_to_tool_shed_schema_url():
    assert tsch.schema_url(MOTUS_GUID) == (
        "https://toolshed.g2.bx.psu.edu/api/tools/bgruening~data_manager_motus~motus_db_fetcher"
        "/versions/3.1.0+galaxy2/parameter_request_schema"
    )
    with pytest.raises(ValueError):
        tsch.schema_url("toolshed.g2.bx.psu.edu/repos/iuc/repo/tool")
    # $defs names must not contain JSON Pointer escape characters (~1 reads as /).
    assert "~" not in tsch.def_name(MOTUS_GUID) and "/" not in tsch.def_name(MOTUS_GUID)


def test_contributor_schema_drops_unsettable_params_at_every_level():
    raw = json.loads((FIXTURE_SCHEMAS / "bgruening~data_manager_motus~motus_db_fetcher~3.1.0+galaxy2.json").read_text())
    assert raw["properties"]["test_data_manager"]["gx_type"] == "gx_hidden"
    assert raw["required"] == ["test_data_manager"]  # unsatisfiable from a request file
    schema = tsch.contributor_schema(raw)
    assert set(schema["properties"]) == {"version", "db_value"}
    assert "required" not in schema
    assert schema["additionalProperties"] is False
    # Nested (a hidden param inside a conditional branch) and $id/$schema are handled too.
    nested = {
        "$schema": "x",
        "$id": "y",
        "properties": {"c": {"$ref": "#/$defs/W"}},
        "$defs": {"W": {"properties": {"h": {"gx_type": "gx_hidden"}, "k": {"type": "string"}}, "required": ["h", "k"]}},
    }
    out = tsch.contributor_schema(nested)
    assert out["$defs"]["W"] == {"properties": {"k": {"type": "string"}}, "required": ["k"]}
    assert "$id" not in out and "$schema" not in out
    # Dataset/collection inputs come from workflow connections, never from params.
    data = {"properties": {"fasta": {"gx_type": "gx_data"}, "reads": {"gx_type": "gx_data_collection"}, "k": {}}}
    assert list(tsch.contributor_schema(data)["properties"]) == ["k"]


def test_embedding_a_conditional_schema_keeps_its_refs_resolvable():
    name = tsch.def_name(SAMESTR_GUID)
    standalone = fixture_schema(SAMESTR_GUID)
    embedded = tsch.embed(standalone, name)
    assert embedded["properties"]["db_source"]["$ref"] == f"#/$defs/{name}/$defs/ConditionalType"
    mapping = embedded["$defs"]["ConditionalType"]["discriminator"]["mapping"]
    assert mapping["motus"] == f"#/$defs/{name}/$defs/When_db_type_motus"
    assert tsch.unembed(embedded, name) == standalone
    # Validation through the parent document reaches the conditional's branches.
    import jsonschema

    parent = {"$defs": {name: embedded}, "properties": {"params": {"$ref": f"#/$defs/{name}"}}}
    v = jsonschema.Draft202012Validator(parent)
    assert list(v.iter_errors({"params": {"db_source": {"db_type": "motus"}}})) == []
    assert list(v.iter_errors({"params": {"db_source": {"db_type": "kraken"}}})) != []
    # Every local pointer in the committed schema resolves.
    committed = json.loads(tsch.COMMITTED_SCHEMA.read_text())
    assert tsch.unresolvable_refs(committed) == []
    resolver = jsonschema.Draft202012Validator(committed)._resolver
    for ref in tsch.local_refs(committed):
        resolver.lookup(ref)
    # And the detector sees what the server sometimes leaves behind.
    assert tsch.unresolvable_refs({"properties": {"a": {"$ref": "#/components/schemas/X"}}}) == ["#/components/schemas/X"]


def test_embed_refuses_refs_it_cannot_relocate():
    for ref in ("#", "#ConditionalType", "ConditionalType", "other.json#/$defs/X"):
        with pytest.raises(ValueError, match="unsupported"):
            tsch.embed({"properties": {"a": {"$ref": ref}}}, "n")


def test_flatten_params_produces_galaxy_paths():
    assert tsch.flatten_params({"a": {"b": 1, "c": {"d": 2}}, "e": 3}) == {"a|b": 1, "a|c|d": 2, "e": 3}
    assert tsch.flatten_params({}) == {}


def test_params_validation_accepts_real_requests_and_rejects_mistakes():
    motus = fixture_schema(MOTUS_GUID)
    assert tsch.validate_params(motus, {"version": "3.1.0", "db_value": "db_from_2026-04-27T094930Z"}) == []
    assert tsch.validate_params(motus, {}) == []
    [err] = tsch.validate_params(motus, {"version": "9.9.9"})
    assert err == "version: '9.9.9' is not one of ['3.1.0', '3.0.1', '3.0.0']"
    [err] = tsch.validate_params(motus, {"verison": "3.1.0"})
    assert err.startswith("Additional properties are not allowed ('verison' was unexpected)")
    assert err.endswith("this tool's parameters here are ['db_value', 'version']")
    [err] = tsch.validate_params(motus, {"db_value": 5})
    assert err.startswith("db_value: 5 is not")

    samestr = fixture_schema(SAMESTR_GUID)
    assert tsch.validate_params(samestr, {"db_source": {"db_type": "motus"}}) == []
    assert tsch.validate_params(samestr, {"db_source": {"db_type": "metaphlan", "database": "x"}}) == []
    [err] = tsch.validate_params(samestr, {"db_source": {"db_type": "kraken"}})
    assert err == "db_source: db_type: 'kraken' is not one of ['metaphlan', 'motus']"
    [err] = tsch.validate_params(samestr, {"db_source": {"db_type": "motus", "database": "x"}})
    assert "'database' was unexpected" in err and "['db_type', 'motus_db']" in err
    # The flat spelling is rejected with a hint, not silently accepted.
    [err] = tsch.validate_params(samestr, {"db_source|db_type": "motus"})
    assert err == "key 'db_source|db_type': write nested parameters as mappings (db_source: {db_type: ...}), not with '|'"


def test_lint_reports_bad_params_and_unavailable_schema(isolated_requests):
    p = isolated_requests / "motus_db_versioned" / "probe.yaml"
    p.parent.mkdir()
    p.write_text(f"tool_id: {MOTUS_GUID}\ndata_tables: [motus_db_versioned]\nparams:\n  verison: '3.1.0'\n")
    errors = rm.lint_file(p, fixture_schema)
    assert len(errors) == 1 and "params: Additional properties are not allowed ('verison'" in errors[0], errors
    # Same file, no schema check requested: only structural lint.
    assert rm.lint_file(p) == []

    def unavailable(guid):
        raise tsch.SchemaUnavailable("the Tool Shed answered HTTP 503")

    [err] = rm.lint_file(p, unavailable)
    assert "cannot check params" in err and "HTTP 503" in err


def test_model_rejects_empty_data_tables():
    with pytest.raises(Exception):
        rm.Request(tool_id=MOTUS_GUID, data_tables=[])


def test_schema_source_prefers_committed_defs_and_can_refuse_to_fetch(tmp_path):
    committed = tmp_path / "request.schema.json"
    name = tsch.def_name(MOTUS_GUID)
    committed.write_text(json.dumps({"$defs": {name: tsch.embed(fixture_schema(MOTUS_GUID), name)}}))
    src = tsch.SchemaSource(committed=committed, fetch=False)
    assert src(MOTUS_GUID) == fixture_schema(MOTUS_GUID)  # un-embedded back to standalone form
    with pytest.raises(tsch.SchemaUnavailable, match="generate_schema.py"):
        src(SAMESTR_GUID)


def test_committed_request_schema_is_current():
    """schemas/request.schema.json == model + tool schemas: fixtures for the GUIDs in use, committed for the rest.

    The installed-data-manager GUIDs (schemas/data_managers.yml) have no fixtures,
    so their $defs are taken from the committed file itself; CI's
    ``generate_schema.py --check --refresh`` is what verifies those against the
    Tool Shed. Here the model part and every request file's tool are covered.
    """
    committed = tsch.SchemaSource(fetch=False)

    def resolve(guid):
        try:
            return fixture_schema(guid)
        except tsch.SchemaUnavailable:
            return committed(guid)

    expected = gs.render(gs.build_schema(gs.all_tool_ids(), resolve, required=set(gs.tool_ids_in_use())))
    assert tsch.COMMITTED_SCHEMA.read_text() == expected, "run: python scripts/generate_schema.py"
    assert set(gs.tool_ids_in_use()) <= set(gs.all_tool_ids())


def test_committed_request_schema_validates_the_request_files_as_editors_would():
    import jsonschema

    schema = json.loads(tsch.COMMITTED_SCHEMA.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    for path in rm.iter_request_files():
        doc = rm.yaml.safe_load(path.read_text())  # exactly as written, no transformation
        assert list(validator.iter_errors(doc)) == [], path
    # The if/then splice is live: a wrong param for this tool_id fails at the document level.
    bad = {"tool_id": MOTUS_GUID, "data_tables": ["motus_db_versioned"], "params": {"version": "9.9.9"}}
    assert list(validator.iter_errors(bad)) != []
    chained = {"tool_id": SAMESTR_GUID, "data_tables": ["samestr_db"], "params": {"db_source": {"db_type": "motus"}}}
    assert list(validator.iter_errors(chained)) == []
    unpinned = {"tool_id": "toolshed.g2.bx.psu.edu/repos/iuc/repo/tool", "data_tables": ["t"]}
    assert any(e.validator == "pattern" for e in validator.iter_errors(unpinned))
    assert any(e.validator == "minItems" for e in validator.iter_errors({"tool_id": MOTUS_GUID, "data_tables": []}))


# --------------------------------------------------------------------------- #
# generate_build
# --------------------------------------------------------------------------- #
def test_standalone_build_single_bundle_step():
    request, dm, version = gb.load_request(SEEDS["motus"])
    workflow, job = gb.build(request, dm, version)
    assert list(workflow["steps"]) == ["motus_db_versioned"]
    step = workflow["steps"]["motus_db_versioned"]
    assert step["tool_state"]["__data_manager_mode"] == "bundle"
    assert workflow["outputs"] == {"motus_db_versioned_bundle": {"outputSource": "motus_db_versioned/out_file"}}
    assert job == {"version": "3.1.0", "db_value": "db_from_2026-04-27T094930Z"}
    gb.validate_workflow(workflow)  # gxformat2 strict + native + lint


def test_chained_build_wires_upstream_bundle():
    request, dm, version = gb.load_request(SEEDS["samestr"])
    workflow, _ = gb.build(request, dm, version)
    assert list(workflow["steps"]) == ["metaphlan_database_versioned", "samestr_db"]
    samestr = workflow["steps"]["samestr_db"]
    # structural selector baked, database wired from the metaphlan step's bundle
    assert samestr["tool_state"]["db_source"]["db_type"] == "metaphlan"
    assert samestr["in"]["db_source|database"]["source"] == "metaphlan_database_versioned/out_file"
    # both bundles exposed as workflow outputs
    assert set(workflow["outputs"]) == {"metaphlan_database_versioned_bundle", "samestr_db_bundle"}
    gb.validate_workflow(workflow)


def test_chained_build_references_existing_upstream(monkeypatch):
    # When the upstream metaphlan already exists, reference it instead of rebuilding.
    monkeypatch.setattr(cde, "resolve_existing_value", lambda url, table, version: "mpa_vJan21_CHOCOPhlAnSGB_202103-04042023")
    request, dm, version = gb.load_request(SEEDS["samestr"])
    workflow, job = gb.build(request, dm, version, reference_galaxy="https://test.galaxyproject.org")
    # single step (samestr only) - no metaphlan build step
    assert list(workflow["steps"]) == ["samestr_db"]
    samestr = workflow["steps"]["samestr_db"]
    assert samestr["tool_state"]["db_source"]["db_type"] == "metaphlan"
    # database wired from a workflow input carrying the existing table value
    assert samestr["in"]["db_source|database"]["source"] == "db_source_database"
    assert job["db_source_database"] == "mpa_vJan21_CHOCOPhlAnSGB_202103-04042023"
    assert set(workflow["outputs"]) == {"samestr_db_bundle"}
    gb.validate_workflow(workflow)


def test_validate_rejects_broken_connection():
    workflow = {
        "class": "GalaxyWorkflow",
        "inputs": {},
        "outputs": {"b": {"outputSource": "s/out_file"}},
        "steps": {
            "s": {
                "tool_id": "toolshed.g2.bx.psu.edu/repos/iuc/r/t/1",
                "tool_version": "1",
                "tool_state": {"__data_manager_mode": "bundle"},
                "in": {"x": {"source": "nonexistent"}},
            }
        },
    }
    with pytest.raises(ValueError):
        gb.validate_workflow(workflow)


# --------------------------------------------------------------------------- #
# get_bundle_urls
# --------------------------------------------------------------------------- #
STANDALONE_INV = {"outputs": {"motus_db_versioned_bundle": {"id": "ds1", "src": "hda"}}}
CHAIN_INV = {
    "outputs": {
        "metaphlan_database_versioned_bundle": {"id": "dsMETA", "src": "hda"},
        "samestr_db_bundle": {"id": "dsSAM", "src": "hda"},
        "some_report": {"id": "dsREP", "src": "hda"},
    }
}


def test_resolve_standalone_bundle():
    assert gburls.bundle_dataset_ids_from_invocation(STANDALONE_INV) == {"motus_db_versioned_bundle": "ds1"}


def test_resolve_chain_returns_both_bundles_excluding_non_bundle():
    result = gburls.bundle_dataset_ids_from_invocation(CHAIN_INV)
    assert result == {"metaphlan_database_versioned_bundle": "dsMETA", "samestr_db_bundle": "dsSAM"}


def test_resolve_empty_suffix_takes_all_hda_outputs():
    assert set(gburls.bundle_dataset_ids_from_invocation(CHAIN_INV, suffix="")) == {
        "metaphlan_database_versioned_bundle",
        "samestr_db_bundle",
        "some_report",
    }


def test_bundle_url_format_and_trailing_slash():
    assert (
        gburls.bundle_url("https://test.galaxyproject.org/", "dsX")
        == "https://test.galaxyproject.org/api/datasets/dsX/display?to_ext=data_manager_json"
    )


# --------------------------------------------------------------------------- #
# import_bundles
# --------------------------------------------------------------------------- #
def test_import_command_assembly():
    cmd = imp.import_command("galaxy-import-data-bundle", "/cvmfs/idc.galaxyproject.org", "http://u/bundle")
    assert cmd == [
        "galaxy-import-data-bundle",
        "--tool-data-path",
        "/cvmfs/idc.galaxyproject.org/data",
        "--data-table-config-path",
        "/cvmfs/idc.galaxyproject.org/config/tool_data_table_conf.xml",
        "http://u/bundle",
    ]


# --------------------------------------------------------------------------- #
# check_data_exists
# --------------------------------------------------------------------------- #
_META_TABLE = {
    "columns": ["value", "name", "dbkey", "path", "db_version"],
    "fields": [["mpa_vJan21_CHOCOPhlAnSGB_202103-04042023", "n", "mpa_vJan21_CHOCOPhlAnSGB_202103", "/p", "SGB"]],
}
_MOTUS_TABLE = {"columns": ["value", "version", "name", "path"], "fields": [["3.1.0", "3.1.0", "n", "/p"]]}


def test_entry_exists_matches_exact_field_and_value_prefix():
    assert cde.entry_exists(_META_TABLE, {"mpa_vJan21_CHOCOPhlAnSGB_202103"})  # dbkey exact + value prefix
    assert not cde.entry_exists(_META_TABLE, {"mpa_vOct22_CHOCOPhlAnSGB_202212"})
    assert cde.entry_exists(_MOTUS_TABLE, {"3.1.0"})  # value exact
    assert not cde.entry_exists(_MOTUS_TABLE, {"3.0.0"})
    assert not cde.entry_exists({"fields": []}, {"anything"})


def test_identity_strings_include_params_and_depends_on():
    req = rm.Request(
        tool_id="toolshed.g2.bx.psu.edu/repos/iuc/data_manager_samestr/samestr_db/1",
        data_tables=["samestr_db"],
        params={},
        depends_on={"metaphlan_database_versioned": "mpa_vJan21_CHOCOPhlAnSGB_202103"},
    )
    ids = cde.identity_strings(req, "marker_db_mpa_vJan21")
    assert "marker_db_mpa_vJan21" in ids and "mpa_vJan21_CHOCOPhlAnSGB_202103" in ids


def test_request_exists_uses_table_lookup(monkeypatch):
    req = gb.load_request(SEEDS["metaphlan"])[0]
    monkeypatch.setattr(cde, "fetch_table", lambda url, table: _META_TABLE)
    assert cde.request_exists(req, "mpa_vJan21_CHOCOPhlAnSGB_202103", "http://g")
    monkeypatch.setattr(cde, "fetch_table", lambda url, table: {"fields": []})
    assert not cde.request_exists(req, "mpa_vJan21_CHOCOPhlAnSGB_202103", "http://g")


def test_unconfigured_table_is_absent_but_unreachable_galaxy_is_unknown(monkeypatch, capsys):
    """A 404 means "this Galaxy has no such table" (a definitive no); anything
    else means we could not ask, which must not pass for "not there"."""
    req = gb.load_request(SEEDS["metaphlan"])[0]

    monkeypatch.setattr(cde, "fetch_table", lambda url, table: None)  # 404
    assert not cde.request_exists(req, "mpa_vJan21_CHOCOPhlAnSGB_202103", "http://g")
    assert "is not configured on" in capsys.readouterr().err

    def _boom(url, table):
        raise cde.CheckUnavailable(f"{url}/api/tool_data/{table}: timed out")

    monkeypatch.setattr(cde, "fetch_table", _boom)
    with pytest.raises(cde.CheckUnavailable, match="timed out"):
        cde.request_exists(req, "mpa_vJan21_CHOCOPhlAnSGB_202103", "http://g")


def _candidates(tmp_path, *paths) -> str:
    f = tmp_path / "candidates.txt"
    f.write_text("".join(f"{p}\n" for p in paths) + "\n")  # trailing blank line too
    return str(f)


def test_print_new_selects_the_requests_still_needing_a_build(tmp_path, monkeypatch, capsys):
    """The build stage's only gate: print requests whose data is absent, drop
    (and report) the ones already present, ignore paths that are not files."""
    monkeypatch.setattr(
        cde, "fetch_table", lambda url, table: _MOTUS_TABLE if table == "motus_db_versioned" else {"fields": []}
    )
    listing = _candidates(tmp_path, SEEDS["motus"], SEEDS["metaphlan"], tmp_path / "gone.yaml")

    assert cde.main(["--from-file", listing, "--print-new", "--reference-galaxy", "http://g"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [str(SEEDS["metaphlan"])]  # motus exists, gone.yaml dropped
    assert "skip motus_db_versioned/3.1.0" in err


def test_print_new_on_empty_input_builds_nothing(tmp_path, capsys):
    assert cde.main(["--from-file", _candidates(tmp_path), "--print-new"]) == 0
    assert capsys.readouterr().out == ""


def test_print_new_fails_rather_than_rebuilding_when_galaxy_cannot_answer(tmp_path, monkeypatch, capsys):
    """Fail the build step loudly: treating "don't know" as "absent" would
    rebuild data that already exists (hours of Galaxy compute)."""
    def _boom(url, table):
        raise cde.CheckUnavailable(f"{url}/api/tool_data/{table}: [Errno 60] timed out")

    monkeypatch.setattr(cde, "fetch_table", _boom)
    listing = _candidates(tmp_path, SEEDS["motus"])

    assert cde.main(["--from-file", listing, "--print-new", "--reference-galaxy", "http://g"]) == 1
    out, err = capsys.readouterr()
    assert out == ""  # nothing is offered for building
    assert "cannot tell whether this already exists" in err


def test_expect_exists_verifies_the_identity_we_key_on(tmp_path, monkeypatch, capsys):
    """Post-import check: the version identity in the request path must be
    findable in the row the data manager actually wrote."""
    monkeypatch.setattr(cde, "fetch_table", lambda url, table: _MOTUS_TABLE)
    assert cde.main([str(SEEDS["motus"]), "--expect-exists", "--reference-galaxy", "http://g"]) == 0
    assert "ok: motus_db_versioned/3.1.0 is present" in capsys.readouterr().out

    monkeypatch.setattr(cde, "fetch_table", lambda url, table: {"fields": []})
    assert cde.main([str(SEEDS["motus"]), "--expect-exists", "--reference-galaxy", "http://g"]) == 1
    assert "does not match the entry that was written" in capsys.readouterr().err


def test_lint_mode_warns_without_failing(tmp_path, monkeypatch, capsys):
    """The PR lint is informational: it annotates and still exits 0."""
    monkeypatch.setattr(cde, "fetch_table", lambda url, table: _MOTUS_TABLE)
    assert cde.main([str(SEEDS["motus"]), "--warn", "--reference-galaxy", "http://g"]) == 0
    assert "::warning:: motus_db_versioned/3.1.0 already exists" in capsys.readouterr().err
    # ... and fails without --warn
    assert cde.main([str(SEEDS["motus"]), "--reference-galaxy", "http://g"]) == 1


class _FakeGi:
    """Minimal stand-in for a bioblend GalaxyInstance for history resolution."""

    def __init__(self, invocations, invocation_detail, datasets=None):
        self._invocations = invocations
        self._invocation_detail = invocation_detail
        self._datasets = datasets or []
        # dataset id -> state for show_dataset (default "ok")
        self.dataset_states: dict[str, str] = {}

        outer = self

        class _Histories:
            def get_histories(self, name, deleted=False):
                return [{"id": "hist1", "name": name}]

        class _Invocations:
            def get_invocations(self, history_id):
                return outer._invocations

            def show_invocation(self, invocation_id):
                return outer._invocation_detail

        class _Datasets:
            def get_datasets(self, history_id, extension, order):
                return outer._datasets

            def show_dataset(self, dataset_id):
                return {"id": dataset_id, "state": outer.dataset_states.get(dataset_id, "ok")}

        self.histories = _Histories()
        self.invocations = _Invocations()
        self.datasets = _Datasets()


def test_history_resolution_prefers_latest_invocation():
    gi = _FakeGi(
        invocations=[
            {"id": "old", "create_time": "2026-01-01T00:00:00"},
            {"id": "new", "create_time": "2026-02-01T00:00:00"},
        ],
        invocation_detail=CHAIN_INV,  # named *_bundle outputs
    )
    result = gburls.bundles_from_history(gi, "idc-samestr_db-v1")
    # precise: exactly the two bundle outputs, not a dataset scan
    assert result == {"metaphlan_database_versioned_bundle": "dsMETA", "samestr_db_bundle": "dsSAM"}


def test_history_resolution_returns_empty_when_history_missing():
    class _NoHistory:
        class histories:
            @staticmethod
            def get_histories(name, deleted=False):
                return []

    assert gburls.bundles_from_history(_NoHistory(), "idc-missing-1") == {}


def test_import_skips_gracefully_when_no_bundles(tmp_path, capsys):
    inv = tmp_path / "inv.json"
    inv.write_text('{"outputs": {}}')  # invocation with no bundle outputs
    rc = imp.main([
        "--invocation-json", str(inv),
        "--dm", "motus_db_versioned", "--version", "3.1.0",
        "--cvmfs-root", str(tmp_path),
    ])
    assert rc == 0
    assert "skipping" in capsys.readouterr().out


def test_history_resolution_falls_back_to_dataset_scan_without_invocation():
    gi = _FakeGi(
        invocations=[],
        invocation_detail={},
        datasets=[{"id": "d0"}, {"id": "d1"}],
    )
    result = gburls.bundles_from_history(gi, "idc-motus_db_versioned-3.1.0")
    assert list(result.values()) == ["d0", "d1"]


def test_import_dry_run_and_idempotency(tmp_path, capsys):
    inv = tmp_path / "inv.json"
    inv.write_text('{"outputs":{"samestr_db_bundle":{"id":"dsSAM","src":"hda"}}}')
    args = [
        "--galaxy-url", "https://test.galaxyproject.org",
        "--invocation-json", str(inv),
        "--dm", "samestr_db", "--version", "v1",
        "--cvmfs-root", str(tmp_path),
    ]
    # dry-run imports nothing and writes no marker
    assert imp.main(args + ["--dry-run"]) == 0
    assert not imp.record_marker(str(tmp_path), "samestr_db", "v1").exists()

    # write the marker directly, then a real run must skip (idempotent)
    marker = imp.record_marker(str(tmp_path), "samestr_db", "v1")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("done\n")
    assert imp.main(args) == 0
    assert "skipping" in capsys.readouterr().out


def test_chain_import_skips_published_upstream_and_records_it(tmp_path, capsys):
    """A chained build also carries its upstream bundle. With --request, that
    bundle is skipped when the upstream request already published the database,
    and otherwise imported once with the upstream's own record marker written."""
    inv = tmp_path / "inv.json"
    inv.write_text(
        '{"outputs":{"motus_db_versioned_bundle":{"id":"dsMOTUS","src":"hda"},'
        '"samestr_db_bundle":{"id":"dsSAM","src":"hda"}}}'
    )
    req = tmp_path / "marker_db_motus_3.1.0.yaml"
    req.write_text(
        "tool_id: toolshed.g2.bx.psu.edu/repos/iuc/data_manager_samestr/samestr_db/1.2025.111+galaxy4\n"
        "data_tables: [samestr_db]\n"
        'depends_on: {motus_db_versioned: "3.1.0"}\n'
    )
    args = [
        "--galaxy-url", "https://test.galaxyproject.org",
        "--invocation-json", str(inv),
        "--dm", "samestr_db", "--version", "marker_db_motus_3.1.0",
        "--request", str(req),
        "--cvmfs-root", str(tmp_path),
        "--dry-run",
    ]
    up_marker = imp.record_marker(str(tmp_path), "motus_db_versioned", "3.1.0")

    # upstream not yet published: both bundles import, both markers would be written
    assert imp.main(args) == 0
    out = capsys.readouterr().out
    assert "# import motus_db_versioned_bundle" in out
    assert "# import samestr_db_bundle" in out
    assert f"would record: {up_marker}" in out

    # upstream already published by its own request: only samestr imports
    up_marker.parent.mkdir(parents=True, exist_ok=True)
    up_marker.write_text("done\n")
    assert imp.main(args) == 0
    out = capsys.readouterr().out
    assert "# import motus_db_versioned_bundle" not in out
    assert "# skip motus_db_versioned_bundle" in out
    assert "# import samestr_db_bundle" in out


def test_import_without_request_imports_every_bundle(tmp_path, capsys):
    """Without --request nothing is known about upstreams: behaviour unchanged."""
    inv = tmp_path / "inv.json"
    inv.write_text(
        '{"outputs":{"motus_db_versioned_bundle":{"id":"dsMOTUS","src":"hda"},'
        '"samestr_db_bundle":{"id":"dsSAM","src":"hda"}}}'
    )
    up_marker = imp.record_marker(str(tmp_path), "motus_db_versioned", "3.1.0")
    up_marker.parent.mkdir(parents=True, exist_ok=True)
    up_marker.write_text("done\n")
    assert imp.main([
        "--invocation-json", str(inv),
        "--dm", "samestr_db", "--version", "marker_db_motus_3.1.0",
        "--cvmfs-root", str(tmp_path), "--dry-run",
    ]) == 0
    out = capsys.readouterr().out
    assert "# import motus_db_versioned_bundle" in out
    assert "# import samestr_db_bundle" in out


def test_import_refuses_bundles_that_are_not_ok(tmp_path, capsys):
    """Resolving from a live Galaxy checks dataset states: a still-running or
    failed build must fail the import rather than publish a partial bundle."""
    gi = _FakeGi(
        invocations=[{"id": "inv1", "create_time": "2026-09-20T17:50:00"}],
        invocation_detail={"outputs": {"motus_db_versioned_bundle": {"id": "dsMOTUS", "src": "hda"}}},
    )
    gi.dataset_states["dsMOTUS"] = "running"
    with pytest.raises(SystemExit, match="motus_db_versioned_bundle is 'running'"):
        imp.check_bundles_ready(gi, gburls.bundles_from_history(gi, "idc-motus_db_versioned-3.1.0"))

    gi.dataset_states["dsMOTUS"] = "ok"
    imp.check_bundles_ready(gi, {"motus_db_versioned_bundle": "dsMOTUS"})  # no raise
