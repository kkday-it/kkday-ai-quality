"""Mock 完整數據 — 覆蓋全場景供 demo 完整交互。

覆蓋維度：
- 5 商品（PM 下拉多選項）
- 8 內容 dimension 全覆蓋
- 5 verdict 全覆蓋（real_config_issue / content_missing / content_unclear / customer_misread / escalate_ops）
- 3 感知管道（A_platform / B_customer / C_supplier）× 多 source_system
- 5 status（new / confirmed / dismissed / fixed / data_missing）
- writer_handoff True（可重生）/ False（缺事實需 PM 補）兩條路徑
- 高/低信心（<0.7 觸發「低信心待人工」KPI）

用法：cd backend && .venv/bin/python seed_mock.py
會清空 findings 表後重新塞入 mock。
"""

from __future__ import annotations

from app.core.db import _conn, init_db, insert_findings_batch
from app.core.schema import TicketFinding
from app.judge.diagnose import build_exec

# verdict → 建議動作
ACTION = {
    "real_config_issue": "fix_contradiction",
    "content_missing": "add_missing_info",
    "content_unclear": "clarify_wording",
    "contract_breach": "penalize_breach",
    "customer_misread": "escalate_ux",
    "escalate_ops": "escalate_ops",
}
# 可由 writer 重生的欄位（其餘為「缺事實」需 PM 手動補）
WRITER_FIELDS = {"prod_name", "prod_feature", "prod_summary"}

# (prod_oid, dimension, field, verdict, summary, evidence, ground_truth, conf, status, channel, system, is_primary)
MOCK: list[tuple] = [
    # ── 150665 富士山打卡一日遊 ──
    (
        "150665",
        "行程流程",
        "prod_schedules",
        "content_missing",
        "未說明全程約 4 小時拉車，客人覺得景點停留太趕",
        "行程：河口湖天上山纜車＋忍野八海＋羅森便利店＋淺間公園",
        "客服回覆：實際車程約 4 小時，景點停留偏短屬正常安排",
        0.90,
        "new",
        "B_customer",
        "商品評論",
        True,
    ),
    (
        "150665",
        "承諾與SLA",
        "prod_feature",
        "content_unclear",
        "「贈送纜車體驗」未註明遇強風停駛不另賠償",
        "【1人成團｜贈送纜車體驗】富士山打卡一日遊",
        "客服：纜車遇強風停駛屬天候因素，恕不退費或補償",
        0.85,
        "new",
        "B_customer",
        "FreshDesk工單",
        True,
    ),
    (
        "150665",
        "集合資訊",
        "none",
        "real_config_issue",
        "集合地點寫「新宿站西口」實際應為都廳大樓前",
        "集合地點：新宿站西口",
        "AM 確認：正確集合點為東京都廳第一本廳舍前",
        0.78,
        "confirmed",
        "B_customer",
        "訂單訊息",
        False,
    ),
    (
        "150665",
        "商品定位",
        "prod_name",
        "customer_misread",
        "客人誤以為含午餐，頁面其實已標明不含",
        "費用不含：午餐及個人消費",
        "頁面費用說明已明確標示不含餐，屬呈現未夠醒目",
        0.72,
        "dismissed",
        "A_platform",
        "NPS",
        False,
    ),
    # ── 88102 東京迪士尼海洋一日券 ──
    (
        "88102",
        "使用兌換",
        "pkg_desc",
        "content_missing",
        "未說明電子票需提前於 App 綁定才可入園",
        "",
        "客服：電子票須於官方 App 完成綁定後方可入園",
        0.88,
        "new",
        "B_customer",
        "商品評論",
        True,
    ),
    (
        "88102",
        "費用資訊",
        "prod_feature",
        "real_config_issue",
        "成人票與兒童票價標反，兒童反而較貴",
        "兒童票 NT$2,400／成人票 NT$1,800",
        "正確為成人 NT$2,400、兒童 NT$1,800",
        0.92,
        "confirmed",
        "A_platform",
        "Mixpanel",
        True,
    ),
    (
        "88102",
        "限制與風險",
        "none",
        "escalate_ops",
        "客人反映入園後部分設施維修，屬樂園營運非內容問題",
        "",
        "設施維修為樂園即時公告，轉 CS 安撫並引導官方資訊",
        0.65,
        "new",
        "B_customer",
        "FreshDesk工單",
        False,
    ),
    (
        "88102",
        "承諾與SLA",
        "prod_summary",
        "contract_breach",
        "頁面載明含中文語音導覽機，現場未提供（內容合規但履約不符）",
        "費用包含：中文語音導覽機 ×1",
        "客服：該設施中文導覽機缺貨未補，供應商未依頁面承諾提供",
        0.84,
        "new",
        "B_customer",
        "FreshDesk工單",
        True,
    ),
    # ── 203344 沖繩青之洞窟浮潛 ──
    (
        "203344",
        "限制與風險",
        "prod_summary",
        "content_missing",
        "未列明孕婦／心臟病／高血壓不可參加",
        "",
        "客服：健康狀況限制（孕婦、心血管疾病）須事前告知且不可參加",
        0.91,
        "new",
        "C_supplier",
        "供應商申訴",
        True,
    ),
    (
        "203344",
        "成團條件",
        "none",
        "content_unclear",
        "標「保證出發」但未明寫未滿 2 人會改期",
        "保證出發",
        "實際未滿最低 2 人成團數時會通知改期",
        0.69,
        "new",
        "B_customer",
        "商品評論",
        True,
    ),
    (
        "203344",
        "集合資訊",
        "none",
        "real_config_issue",
        "寫「那霸市區飯店接送」實際僅限指定飯店",
        "提供那霸市區飯店免費接送",
        "AM：接送僅限指定 12 家合作飯店",
        0.80,
        "fixed",
        "C_supplier",
        "Feedback",
        False,
    ),
    (
        "203344",
        "承諾與SLA",
        "prod_feature",
        "contract_breach",
        "頁面承諾專業教練 1:2 貼身指導，旺季實際 1:6（內容合規但履約不符）",
        "專業教練 1:2 貼身指導，確保安全",
        "供應商旺季超收，實際比例 1:6，違反頁面承諾配置",
        0.81,
        "new",
        "C_supplier",
        "供應商申訴",
        True,
    ),
    # ── 91256 京都祇園和服體驗 ──
    (
        "91256",
        "使用兌換",
        "pkg_schedules",
        "content_unclear",
        "歸還時間 18:00 未醒目標示，逾時加價未說明",
        "當日 19:00 前歸還",
        "客服：18:00 後每小時加收 500 日圓，頁面標示不一致",
        0.74,
        "new",
        "B_customer",
        "訂單訊息",
        True,
    ),
    (
        "91256",
        "商品定位",
        "prod_feature",
        "customer_misread",
        "客人以為含髮型設計，實際需另加購",
        "基本和服穿著＋配件",
        "頁面加購區已列髮型設計選項，屬呈現議題",
        0.71,
        "dismissed",
        "A_platform",
        "商品評論抽樣",
        False,
    ),
    (
        "91256",
        "承諾與SLA",
        "prod_summary",
        "content_missing",
        "未說明雨天和服體驗照常進行且不退款",
        "",
        "客服：雨天和服體驗照常，恕不退費",
        0.86,
        "confirmed",
        "B_customer",
        "FreshDesk工單",
        True,
    ),
    # ── 175890 首爾南山塔纜車套票 ──
    (
        "175890",
        "費用資訊",
        "pkg_desc",
        "content_unclear",
        "套票含纜車來回，未寫明部分方案僅單程",
        "南山塔纜車套票",
        "客服：標準方案含來回，特定優惠方案僅單程，標示需區分",
        0.76,
        "new",
        "B_customer",
        "商品評論",
        True,
    ),
    (
        "175890",
        "行程流程",
        "none",
        "escalate_ops",
        "客人抱怨纜車尖峰排隊 2 小時，屬現場營運",
        "",
        "尖峰排隊為現場運能，轉 CS 安撫，非商品內容問題",
        0.60,
        "new",
        "A_platform",
        "NPS",
        False,
    ),
    (
        "175890",
        "限制與風險",
        "none",
        "real_config_issue",
        "營業時間寫 23:00，實際冬季末班纜車 22:00",
        "末班纜車 23:00",
        "AM：冬季（11–2 月）末班纜車為 22:00",
        0.83,
        "new",
        "C_supplier",
        "供應商申訴",
        True,
    ),
    (
        "175890",
        "商品定位",
        "prod_name",
        "content_missing",
        "標題未標示需自行前往南山腳，不含市區接駁",
        "首爾南山塔纜車套票",
        "客服：不含市區至南山纜車站接駁，須自理",
        0.79,
        "new",
        "B_customer",
        "訂單訊息",
        False,
    ),
    (
        "175890",
        "集合資訊",
        "none",
        "content_missing",
        "未提供纜車站詳細位置／GPS，客人找不到入口",
        "",
        "",
        0.55,
        "data_missing",
        "C_supplier",
        "Feedback",
        False,
    ),
]


def _writer_handoff(verdict: str, field: str) -> bool:
    """可重生判定：content_missing 強制 False（防幻覺）；改寫類且欄位可由 writer 重生才 True。"""
    if verdict == "content_missing":
        return False
    if verdict in ("real_config_issue", "content_unclear"):
        return field in WRITER_FIELDS
    return False


def build_mock() -> list[TicketFinding]:
    out: list[TicketFinding] = []
    for i, (
        prod,
        dim,
        field,
        verdict,
        summary,
        ev,
        gt,
        conf,
        status,
        ch,
        sys_,
        primary,
    ) in enumerate(MOCK):
        role, platform = build_exec(verdict)
        out.append(
            TicketFinding(
                finding_id=f"mock-{i:03d}",
                ticket_id=f"tk-{i:03d}",
                prod_oid=prod,
                dimension=dim,
                problem_summary=summary,
                suspected_field=field,
                evidence_quote=ev,
                ground_truth_quote=gt,
                verdict=verdict,
                confidence=conf,
                recommended_action=ACTION[verdict],
                action_detail="",
                writer_handoff=_writer_handoff(verdict, field),
                is_primary=primary,
                status=status,
                created_at=f"2026-06-23T10:{59 - i:02d}:00",
                source_channel=ch,
                source_system=sys_,
                owner_role=role,
                exec_platform=platform,
                order_oid=f"OD20250612{i:03d}" if ch == "B_customer" else "",
                supplier_oid=f"SUP{2000 + (i % 4)}" if ch in ("B_customer", "C_supplier") else "",
            )
        )
    return out


def main() -> None:
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM judgments")
    findings = build_mock()
    n = insert_findings_batch(findings)
    print(f"已塞入 {n} 筆 mock findings，覆蓋 5 商品 / 8 維度 / 5 verdict / 3 感知管道 / 5 status")


if __name__ == "__main__":
    main()
