"""Offline tests for the site's generated catalog page (scripts/site_catalog.py)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import request_models as rm  # noqa: E402
import site_catalog as cat  # noqa: E402


def test_catalog_lists_every_request_under_its_table():
    catalog = cat.render_catalog()
    for path in rm.iter_request_files():
        assert f"### {rm.data_manager_name(path)}" in catalog
        rel = path.relative_to(REPO_ROOT).as_posix()
        assert f"[`{rm.version_id(path)}`]({cat.REPO_URL}/blob/main/{rel})" in catalog


def test_chained_request_links_its_upstream_table():
    catalog = cat.render_catalog()
    assert "[motus_db_versioned](#motus_db_versioned) 3.1.0" in catalog


def test_catalog_covers_tables_installed_data_managers_and_genomes():
    catalog = cat.render_catalog()
    assert "| `motus_db_versioned` | value, version, name, path | `motus_db_versioned.loc` |" in catalog
    # a requested data manager is marked, an unrequested one is not
    assert "[bgruening/data_manager_motus](https://toolshed.g2.bx.psu.edu/view/bgruening/data_manager_motus)" in catalog
    motus_row = next(line for line in catalog.splitlines() if "view/bgruening/data_manager_motus)" in line)
    assert motus_row.endswith("| yes |")
    diamond_row = next(line for line in catalog.splitlines() if "data_manager_diamond_database_builder)" in line)
    assert diamond_row.endswith("|  |")
    assert "| `dm6` | (from UCSC) | ucsc |" in catalog


def test_cells_stay_on_one_line_and_escape_pipes():
    assert cat._cell("a |\n b") == "a \\| b"
    assert cat._cell(None) == ""


def test_hook_fills_only_the_catalog_page():
    page = SimpleNamespace(file=SimpleNamespace(src_uri="catalog.md"))
    other = SimpleNamespace(file=SimpleNamespace(src_uri="index.md"))
    source = f"# Catalog\n\n{cat.CATALOG_MARKER}\n"
    assert "## Versioned reference data" in cat.on_page_markdown(source, page, None, None)
    assert cat.on_page_markdown(source, other, None, None) == source
    with pytest.raises(ValueError):
        cat.on_page_markdown("# Catalog\n", page, None, None)
