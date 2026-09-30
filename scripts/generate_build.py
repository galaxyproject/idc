#!/usr/bin/env python3
"""Generate a Galaxy data-manager *bundle* workflow from an IDC request file.

Given a request at ``data-managers/<dm>/<version>.yaml`` this emits a gxformat2
workflow (``class: GalaxyWorkflow``) whose data-manager tool steps each run in
``__data_manager_mode: bundle``, plus the planemo job file supplying the build
parameters. Running that workflow on a Galaxy server (Stage 2, via planemo)
produces the reference-data *bundle* dataset(s) that Jenkins later imports onto
CVMFS.

Build parameters are **exposed as workflow inputs** and connected to the tool
parameters (a ``string`` input feeds a ``select`` parameter just fine - see
Galaxy's ``lib/galaxy_test/workflow/multiple_text.gxwf.yml``); their concrete
values live in the generated ``job.yml``. Only the structural selector of a
chained tool (e.g. samestr's ``db_source.db_type``) is baked into ``tool_state``.

Chained builds (a request with ``depends_on``) become multi-step workflows: the
upstream data manager runs first (also in bundle mode) and its ``out_file``
bundle is wired into the downstream tool's data-table-backed input - the pattern
Galaxy's ``test_data_manager_workflow_bundle`` integration test uses to feed a
fetched genome into an indexer.

Usage::

    python scripts/generate_build.py data-managers/motus_db_versioned/3.1.0.yaml
    python scripts/generate_build.py data-managers/samestr_db/marker_db_mpa_vJan21.yaml --outdir build

For each request it writes ``<outdir>/<dm>/<version>/workflow.gxwf.yml`` and
``job.yml`` and prints the planemo command to run it.
"""
import argparse
import copy
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from request_models import (  # noqa: E402
    DATA_MANAGERS_DIR,
    Request,
    data_manager_name,
    iter_request_files,
    version_id,
)
from tool_schemas import flatten_params  # noqa: E402

# How to wire an upstream bundle into a downstream (chained) data manager. Keyed
# on (downstream data manager, upstream data table). ``db_type`` selects the
# branch of the downstream tool's conditional (baked into tool_state);
# ``connect_param`` is the downstream input parameter (gxformat2 ``|`` notation)
# that receives the upstream step's bundle output.
CHAIN_WIRING = {
    ("samestr_db", "metaphlan_database_versioned"): {
        "tool_state": {"db_source": {"db_type": "metaphlan"}},
        "connect_param": "db_source|database",
    },
    ("samestr_db", "motus_db_versioned"): {
        "tool_state": {"db_source": {"db_type": "motus"}},
        "connect_param": "db_source|motus_db",
    },
}

OUT_FILE = "out_file"  # every data manager tool's bundle output


def tool_version_of(tool_id: str) -> str:
    """The trailing version component of a version-pinned toolshed GUID."""
    return tool_id.rsplit("/", 1)[1]


def deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_request(path: Path) -> tuple[Request, str, str]:
    doc = yaml.safe_load(path.read_text())
    return Request(**doc), data_manager_name(path), version_id(path)


class WorkflowBuilder:
    """Accumulates gxformat2 steps, workflow inputs, and the planemo job."""

    def __init__(self) -> None:
        self.steps: dict = {}
        self.inputs: dict = {}
        self.outputs: dict = {}
        self.job: dict = {}

    def add_step(
        self,
        step_key: str,
        tool_id: str,
        params: dict,
        *,
        baked_state: dict | None = None,
        connections: dict | None = None,
        input_prefix: str = "",
    ) -> None:
        tool_state = dict(baked_state or {})
        tool_state["__data_manager_mode"] = "bundle"

        in_map: dict = {}
        # Each build parameter -> a workflow input (string), connected to the
        # tool parameter and given its value in the job file. Request files
        # nest params like the tool form; workflows connect by a|b paths.
        for param_path, value in flatten_params(params).items():
            input_name = (input_prefix + param_path).replace("|", "_")
            self.inputs[input_name] = {"type": "string"}
            self.job[input_name] = value
            in_map[param_path] = {"source": input_name}
        # Upstream-bundle connections (chained builds).
        for param_path, source in (connections or {}).items():
            in_map[param_path] = {"source": source}

        step: dict = {
            "tool_id": tool_id,
            "tool_version": tool_version_of(tool_id),
            "tool_state": tool_state,
        }
        if in_map:
            step["in"] = in_map
        self.steps[step_key] = step
        # Expose this data manager's bundle as a named workflow output, so the
        # invocation surfaces the bundle dataset directly (consumed in Stage 3).
        self.outputs[f"{step_key}_bundle"] = {"outputSource": f"{step_key}/{OUT_FILE}"}

    def workflow(self, label: str) -> dict:
        return {
            "class": "GalaxyWorkflow",
            "label": label,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "steps": self.steps,
        }


def _resolve_request_path(dm: str, version: str) -> Path:
    for ext in (".yaml", ".yml"):
        candidate = DATA_MANAGERS_DIR / dm / f"{version}{ext}"
        if candidate.exists():
            return candidate
    raise SystemExit(
        f"Cannot resolve upstream request data-managers/{dm}/{version}.yaml - "
        f"it must exist so the chained build's upstream step can be generated."
    )


def build(request: Request, dm: str, version: str, reference_galaxy: str | None = None) -> tuple[dict, dict]:
    """Return (gxformat2 workflow dict, planemo job dict) for one request.

    For a chained request (``depends_on``): if ``reference_galaxy`` is given and
    the upstream database already exists in that Galaxy's data table, the existing
    entry is referenced directly (no upstream build step). Otherwise the upstream
    data manager is added as a step and rebuilt.
    """
    from check_data_exists import resolve_existing_value

    wb = WorkflowBuilder()

    connections: dict = {}
    baked_state: dict = {}
    for up_table, up_version in (request.depends_on or {}).items():
        wiring = CHAIN_WIRING.get((dm, up_table))
        if wiring is None:
            raise SystemExit(
                f"No chain wiring defined for downstream {dm!r} depending on {up_table!r}. "
                f"Add an entry to CHAIN_WIRING in scripts/generate_build.py."
            )
        baked_state = deep_merge(baked_state, wiring["tool_state"])

        existing_value = (
            resolve_existing_value(reference_galaxy, up_table, up_version) if reference_galaxy else None
        )
        if existing_value is not None:
            # Reference the already-built upstream entry via a workflow input.
            input_name = wiring["connect_param"].replace("|", "_")
            wb.inputs[input_name] = {"type": "string"}
            wb.job[input_name] = existing_value
            connections[wiring["connect_param"]] = input_name
        else:
            # Rebuild the upstream data manager as a step and wire its bundle.
            up_request, up_dm, _ = load_request(_resolve_request_path(up_table, up_version))
            wb.add_step(up_dm, up_request.tool_id, up_request.params, input_prefix=f"{up_dm}_")
            connections[wiring["connect_param"]] = f"{up_dm}/{OUT_FILE}"

    wb.add_step(
        dm,
        request.tool_id,
        request.params,
        baked_state=baked_state or None,
        connections=connections or None,
    )
    return wb.workflow(f"IDC bundle: {dm} {version}"), wb.job


def validate_workflow(workflow: dict) -> None:
    """Validate a generated gxformat2 workflow with gxformat2 itself.

    Runs three checks and raises ValueError on the first failure:
      1. strict schema validation (Format2StrictModel),
      2. format2 -> native conversion (structural / connection sanity),
      3. the core gxformat2 semantic linter.
    """
    try:
        from gxformat2 import python_to_workflow
        from gxformat2.lint import (
            Format2StrictModel,
            LintContext,
            lint_format2,
        )
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ValueError(
            "gxformat2 is required to validate generated workflows; install it "
            "(it ships with planemo) or pass --no-validate."
        ) from exc

    try:
        Format2StrictModel(**workflow)
    except Exception as exc:
        raise ValueError(f"gxformat2 strict schema validation failed: {exc}") from exc

    try:
        python_to_workflow(copy.deepcopy(workflow))
    except Exception as exc:
        raise ValueError(f"gxformat2 format2->native conversion failed: {exc}") from exc

    lint_context = LintContext(level="error")
    lint_format2(lint_context, workflow, raw_dict=workflow)
    if lint_context.found_errors:
        messages = "; ".join(str(m) for m in lint_context.error_messages)
        raise ValueError(f"gxformat2 lint reported errors: {messages}")


def write_build(
    request: Request,
    dm: str,
    version: str,
    outdir: Path,
    validate: bool = True,
    reference_galaxy: str | None = None,
) -> Path:
    workflow, job = build(request, dm, version, reference_galaxy=reference_galaxy)
    if validate:
        validate_workflow(workflow)
    build_dir = outdir / dm / version
    build_dir.mkdir(parents=True, exist_ok=True)
    wf_path = build_dir / "workflow.gxwf.yml"
    (wf_path).write_text(yaml.safe_dump(workflow, sort_keys=False))
    (build_dir / "job.yml").write_text(yaml.safe_dump(job, sort_keys=False) if job else "{}\n")
    return wf_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", nargs="*", help="Request YAML file(s) under data-managers/")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process every request file under data-managers/ (instead of listing them)",
    )
    parser.add_argument("--outdir", default="build", help="Where to write generated builds (default: build/)")
    parser.add_argument(
        "--galaxy-url",
        default="https://test.galaxyproject.org",
        help="Galaxy URL for the printed planemo command",
    )
    parser.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="Skip gxformat2 validation of the generated workflow",
    )
    parser.add_argument(
        "--reference-galaxy",
        default=None,
        help=(
            "If a chained request's upstream database already exists in this "
            "Galaxy's data table, reference it instead of rebuilding it."
        ),
    )
    args = parser.parse_args(argv)

    if args.all:
        request_paths = iter_request_files()
    elif args.requests:
        request_paths = [Path(r).resolve() for r in args.requests]
    else:
        parser.error("provide request file(s) or --all")

    outdir = Path(args.outdir)
    for path in request_paths:
        request, dm, version = load_request(path)
        wf_path = write_build(
            request, dm, version, outdir, validate=args.validate, reference_galaxy=args.reference_galaxy
        )
        job_path = wf_path.parent / "job.yml"
        history = f"idc-{dm}-{version}"
        print(f"# {dm} {version}")
        print(f"planemo run {wf_path} {job_path} \\")
        print(f"  --galaxy_url {args.galaxy_url} --galaxy_user_key $TEST_GALAXY_KEY \\")
        print(f'  --history_name "{history}" --tags idc --no_wait --no_use_cache \\')
        print(f"  --output_json {wf_path.parent / 'invocation.json'}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
