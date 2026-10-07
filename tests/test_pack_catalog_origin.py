from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from rankeddojo.adapters.pack.local_pack_catalog import LocalPackCatalog


def write_pack(root: Path, pack_id: str) -> None:
    root.mkdir(parents=True)
    (root / "pack.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "id": pack_id,
                "name": pack_id,
                "version": "1.0.0",
                "levels": [{"id": "level0", "path": "level0"}],
            }
        ),
        encoding="utf-8",
    )


class PackCatalogOriginTest(unittest.TestCase):
    def test_origin_uses_catalog_root_not_display_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = root / "managed"
            bundled = root / "bundled"
            write_pack(managed / "user_content", "user_content")
            write_pack(bundled / "example_content", "example_content")
            catalog = LocalPackCatalog(managed, bundled_packs_dir=bundled)

            self.assertEqual(catalog.pack_origin("user_content"), "managed")
            self.assertEqual(catalog.pack_origin("example_content"), "embedded")
            self.assertEqual([pack.id for pack in catalog.list_managed_packs()], ["user_content"])


if __name__ == "__main__":
    unittest.main()
