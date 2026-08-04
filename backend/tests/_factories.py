"""測試資料工廠：把「DB 列長什麼樣」「finding_id 怎麼組」收斂成單一定義。

**存在的理由**：`_pr_row` 原本在 5 個測試檔各有一份幾乎相同的複製（兩種欄位集合），
重複的測試資料建構散落多檔。這類重複的代價在 schema 演進時
才會爆出來——來源表欄位一改、finding_id 規則一變，就要在幾十個地方逐一手改，漏一處就是一支
莫名其妙的紅燈。集中之後，schema 變更只需要改這個檔案。

⚠️ 這裡的欄名/格式刻意跟著**當前** schema 走，不做相容分支：測試要反映現況，不是反映歷史。
"""

from __future__ import annotations


def review_row(rec_oid: str = "REC1", **overrides) -> dict:
    """建一筆 reviews 源列（源欄名、值皆 Text，對齊拆表後 schema）。

    預設值涵蓋列表篩選/導出會用到的欄位；各測試以 `**overrides` 覆寫自己在意的欄。

    Args:
        rec_oid: 評論特徵 id（自然鍵）。
        **overrides: 欲覆寫的源欄位。

    Returns:
        可直接餵給 `db.insert_source_batch("reviews", [...])` 的列 dict。
    """
    return {
        "rec_oid": rec_oid,
        "member_uuid": "U1",
        "create_date": "2026-06-01 10:00:00",
        "rec_title": "標題",
        "rec_desc": "內容",
        "rec_scores": "5",
        "traveller_type": "solo",
        "lang_code": "zh-tw",
        "prod_oid": "P1",
        "pkg_oid": "PKG1",
        "order_oid": "O1",
        "order_mid": "M1",
        "supplier_oid": "S1",
        "order_snap_json": "{}",
        "go_date": "2026-07-01",
        **overrides,
    }
