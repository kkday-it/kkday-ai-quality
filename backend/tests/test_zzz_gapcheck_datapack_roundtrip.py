"""臨時缺口驗證：資料包 export→import 是否完整帶走本輪三張新表（跑完即刪，不入版本庫）。"""

from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import func, select

from app.core.db import datapack as DP
from app.core.db import tables as T


def test_roundtrip_new_tables(temp_db):
    eng = T.get_engine()
    with eng.begin() as conn:
        conn.execute(
            T.attributions.insert().values(
                source="reviews",
                source_id="RT1",
                l1_code="C-1",
                l2_code="C-1-1",
                polarity="negative",
                is_deleted=True,
                is_human_corrected=True,
                conf_tier="human",
            )
        )
        conn.execute(
            T.attribution_suggestions.insert().values(
                feedback_source_code="reviews",
                source_id="RT1",
                suggestion_batch_id="rt-batch-1",
                change_type="add",
                l1_code="C-2",
                l2_code="C-2-1",
            )
        )
        conn.execute(
            T.attribution_notes.insert().values(
                source="reviews",
                source_id="RT1",
                l1_code="C-1",
                l2_code="C-1-1",
                note_type="follow_up",
                content="rt-note",
            )
        )
        conn.execute(
            T.attribution_dimensions.insert().values(
                dimension_code="severity", item_code="rt_sev", item_label="RT", sort_order=99
            )
        )

    built = DP.build_datapack(include_sensitive=False)
    zip_bytes = built[0] if isinstance(built, tuple) else built
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    members = zf.namelist()
    print("MEMBERS:", sorted(members))
    manifest = json.loads(zf.read("manifest.json"))
    print("MANIFEST:", json.dumps(manifest, ensure_ascii=False)[:1500])
    for t in ("attribution_suggestion_lst", "attribution_note_lst", "attribution_dimension_master"):
        assert f"tables/{t}.ndjson" in members, f"{t} 未進資料包"
        print(t, "ROW:", zf.read(f"tables/{t}.ndjson").decode()[:400])

    res = DP.load_datapack(zip_bytes, include_sensitive=False)
    print("LOAD RESULT:", res)

    with eng.begin() as conn:
        for t in (
            T.attributions,
            T.attribution_suggestions,
            T.attribution_notes,
            T.attribution_dimensions,
        ):
            n = conn.execute(select(func.count()).select_from(t)).scalar()
            print("AFTER", t.name, n)
            assert n == 1, f"{t.name} 匯入後列數 {n}"
        # 序列是否推到 max(id) 之後：再插一筆不得撞主鍵
        conn.execute(
            T.attribution_notes.insert().values(
                source="reviews",
                source_id="RT2",
                l1_code="C-1",
                l2_code="C-1-1",
                note_type="follow_up",
                content="after-import",
            )
        )
        conn.execute(
            T.attribution_suggestions.insert().values(
                feedback_source_code="reviews",
                source_id="RT2",
                suggestion_batch_id="rt-batch-2",
                change_type="add",
            )
        )
        conn.execute(
            T.attribution_dimensions.insert().values(
                dimension_code="severity", item_code="rt_sev2", item_label="RT2", sort_order=98
            )
        )
    print("POST-IMPORT INSERTS OK")
