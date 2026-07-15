#!/usr/bin/env node
/* C3～C6 Mock＋Auditor＋Judge checkpoint 总账；仅使用 @oai/artifact-tool。 */

import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.join(process.cwd(), "artifact-work.cjs"));
const { FileBlob, SpreadsheetFile, Workbook } = require("@oai/artifact-tool");

function argsOf(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i].startsWith("--")) out[argv[i].slice(2)] = argv[i + 1] ?? "";
  }
  return out;
}

function readJsonl(text) {
  return text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
}

async function loadJsonl(file) {
  try {
    return readJsonl(await fs.readFile(file, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

function colLetter(index) {
  let n = index + 1;
  let out = "";
  while (n) {
    n -= 1;
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26);
  }
  return out;
}

function join(values, separator = "｜") {
  return (values || []).filter((x) => x !== null && x !== undefined && x !== "").join(separator) || "—";
}

function sameSet(a = [], b = []) {
  return [...a].sort().join("|") === [...b].sort().join("|");
}

function categoryOf(c) {
  if (c.expected_domain === "uncertain" || c.layer === 2) return "boundary";
  return c.expected_domain === "true" ? "match" : "nonmatch";
}

function categoryLabel(c) {
  if (c.expected_domain === "uncertain") return `存疑·證據不足／${c.case_family}`;
  if (c.layer === 2) return `邊界·${c.case_family}${c.boundary_with ? `（vs ${c.boundary_with}）` : ""}`;
  return `${c.expected_domain === "true" ? "符合" : "不符合"}·${c.case_family}`;
}

function expectedLabel(value) {
  return value === "true" ? "符合" : value === "false" ? "不符合" : "存疑";
}

function polarityLabel(value) {
  return value === "negative" ? "負向" : value === "positive" ? "正向" : "中立";
}

function judgeStatus(r) {
  if (!r) return "未執行";
  if (!r.error) return "成功";
  if (String(r.error).includes("exceeded your current quota")) return "429額度失敗";
  return "執行失敗";
}

function confidence(r) {
  const values = r?.predicted_confidences || [];
  return values.length ? Math.max(...values) : null;
}

function judgeAssessment(c, r) {
  if (!r) return "—";
  if (r.error) return "⛔無有效結果";
  const predHit = Boolean(r.predicted_domain_hit);
  const domainCorrect = c.expected_domain === "true" ? predHit : !predHit;
  if (!domainCorrect) return "❌域判定不一致";
  if (c.expected_domain === "true" && !sameSet(c.expected_l2_codes, r.predicted_l2_codes || [])) {
    return "⚠️L2有出入";
  }
  if (predHit && r.evidence_grounded === false) return "⚠️證據未落地";
  return "✅完全一致";
}

function reviewRequired(c, a, r) {
  const assessment = judgeAssessment(c, r);
  return Boolean(
    c.layer === 2 ||
    c.expected_domain === "uncertain" ||
    a?.review_required ||
    a?.status === "review_required" ||
    judgeStatus(r) !== "成功" ||
    assessment.startsWith("❌") ||
    assessment.startsWith("⚠️"),
  );
}

const HEADERS = [
  "編號", "受測域", "類別", "表達／難度", "傾向", "評論內容",
  "標準答案", "標準答案·面向", "標準答案·證據",
  "Auditor狀態", "Auditor建議", "Auditor理由",
  "Judge運行", "Judge命中?", "Judge面向", "Judge證據", "Judge信心",
  "AI評核結果", "Judge錯誤", "建議人工抽樣", "人工判定", "人工備註",
];

const LAST_COL = colLetter(HEADERS.length - 1);

function detailRow(c, a, r) {
  const status = judgeStatus(r);
  return [
    c.case_id,
    c.domain_under_test,
    categoryLabel(c),
    `${c.expression_variant}/${c.difficulty}`,
    polarityLabel(c.input_polarity),
    c.text,
    expectedLabel(c.expected_domain),
    join(c.expected_l2_codes),
    join(c.expected_evidence_quotes),
    a?.status || "missing",
    a ? `${a.suggested_domain}｜${join(a.suggested_l2_codes)}` : "—",
    a?.audit_reason || "—",
    status,
    status === "成功" ? (r.predicted_domain_hit ? "是" : "否") : "—",
    status === "成功" ? join(r.predicted_l2_codes) : "—",
    status === "成功" ? join(r.predicted_evidence_quotes) : "—",
    status === "成功" ? confidence(r) : null,
    judgeAssessment(c, r),
    r?.error || "—",
    reviewRequired(c, a, r) ? "是" : "否",
    "",
    "",
  ];
}

function styleHeader(range) {
  range.format = {
    fill: "#173F67",
    font: { bold: true, color: "#FFFFFF", size: 10 },
    verticalAlignment: "center",
    horizontalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: "#102D4A" },
  };
  range.format.rowHeightPx = 42;
}

const WIDTHS = [
  190, 72, 185, 120, 65, 520, 88, 160, 300, 110, 165,
  360, 110, 90, 160, 300, 78, 140, 300, 105, 105, 280,
];

function addDataSheet(workbook, name, rows, tableName, category) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const lastRow = rows.length + 1;
  sheet.getRange(`A1:${LAST_COL}${lastRow}`).values = [HEADERS, ...rows];
  styleHeader(sheet.getRange(`A1:${LAST_COL}1`));

  if (rows.length) {
    const data = sheet.getRange(`A2:${LAST_COL}${lastRow}`);
    data.format = {
      fill: category === "boundary" ? "#FFF2CC" : category === "match" ? "#D9EFF8" : "#EAF6FB",
      font: { size: 9, color: "#1F2937" },
      verticalAlignment: "top",
      wrapText: true,
      borders: {
        insideHorizontal: { style: "thin", color: "#D6E5EC" },
        bottom: { style: "thin", color: "#C6D8E0" },
      },
    };
    data.format.rowHeightPx = 58;
    sheet.getRange(`Q2:Q${lastRow}`).format.numberFormat = "0.00";
    sheet.getRange(`U2:V${lastRow}`).format.fill = "#E2F0D9";
    sheet.getRange(`U2:U${lastRow}`).dataValidation = {
      rule: { type: "list", values: ["對", "錯", "存疑", "待確認"] },
    };
    data.conditionalFormats.add("containsText", {
      text: "❌",
      format: { fill: "#F8CBAD", font: { bold: true, color: "#9C0006" } },
    });
    sheet.getRange(`M2:M${lastRow}`).conditionalFormats.add("containsText", {
      text: "429額度失敗",
      format: { fill: "#F4CCCC", font: { bold: true, color: "#9C0006" } },
    });
    sheet.getRange(`M2:M${lastRow}`).conditionalFormats.add("containsText", {
      text: "未執行",
      format: { fill: "#E7E6E6", font: { color: "#666666" } },
    });
    sheet.getRange(`J2:J${lastRow}`).conditionalFormats.add("containsText", {
      text: "review_required",
      format: { fill: "#FFF2CC", font: { bold: true, color: "#7F6000" } },
    });
    sheet.getRange(`T2:T${lastRow}`).conditionalFormats.add("containsText", {
      text: "是",
      format: { fill: "#FFF2CC", font: { bold: true, color: "#7F6000" } },
    });
  }

  const table = sheet.tables.add(`A1:${LAST_COL}${lastRow}`, true, tableName);
  table.style = "TableStyleLight9";
  table.showFilterButton = true;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(2);
  WIDTHS.forEach((px, i) => {
    sheet.getRange(`${colLetter(i)}1:${colLetter(i)}${lastRow}`).format.columnWidthPx = px;
  });
  return { sheet, lastRow, name, category };
}

function countFormula(defs, col, criterion = null) {
  return defs.map((d) => {
    const range = `'${d.name}'!$${col}$2:$${col}$${d.lastRow}`;
    return criterion === null ? `COUNTA(${range})` : `COUNTIF(${range},"${criterion}")`;
  }).join("+");
}

function addOverview(workbook, domains, sheetDefs, metadata) {
  const sheet = workbook.worksheets.getItem("總覽");
  sheet.showGridLines = false;
  sheet.getRange("A1:H1").merge();
  sheet.getRange("A1").values = [["C3～C6 Gemini 3.5｜Mock＋Auditor＋Judge 目前跑批記錄"]];
  sheet.getRange("A1:H1").format = {
    fill: "#173F67",
    font: { bold: true, color: "#FFFFFF", size: 16 },
    verticalAlignment: "center",
  };
  sheet.getRange("A1:H1").format.rowHeightPx = 42;

  const info = [
    ["資料性質", "AI 合成 Mock；不是人工 Gold，也不代表真實生產分布。"],
    ["目前狀態", "Mock 5,450 條完整；GPT Auditor 5,450 條完整；GPT Judge 因 API 額度中斷，結果不完整。"],
    ["模型", "Generator＝gemini-3.5-flash；Auditor＝gpt-5.5-2026-04-23；Judge＝gpt-5.4-mini-2026-03-17"],
    ["Judge 配置", "temperature=1；reasoning_effort=high；thinking=true；repeats=1"],
    ["Judge Prompt SHA-256", metadata.promptHashes],
    ["參考格式", "C2_Gemini3.5_V2_HighReasoning_判官完整除錯報告.xlsx"],
    ["重要提醒", "紅色＝Judge API 失敗或域判定不一致；黃色＝建議優先人工抽樣；淺綠＝人工填寫區。"],
  ];
  sheet.getRange(`A3:B${info.length + 2}`).values = info;
  sheet.getRange(`A3:A${info.length + 2}`).format = {
    fill: "#D9EAF7",
    font: { bold: true, color: "#173F67" },
    verticalAlignment: "top",
    wrapText: true,
  };
  sheet.getRange(`B3:B${info.length + 2}`).format = { wrapText: true, verticalAlignment: "top" };
  sheet.getRange(`A3:B${info.length + 2}`).format.borders = {
    insideHorizontal: { style: "thin", color: "#C7D8E6" },
  };

  const start = 12;
  sheet.getRange(`A${start}:G${start}`).values = [[
    "受測域", "Mock總數", "Auditor完成", "Judge成功", "429額度失敗", "未執行", "建議人工抽樣",
  ]];
  styleHeader(sheet.getRange(`A${start}:G${start}`));
  domains.forEach((domain, index) => {
    const row = start + 1 + index;
    const defs = sheetDefs.filter((d) => d.domain === domain);
    sheet.getRange(`A${row}`).values = [[domain]];
    sheet.getRange(`B${row}`).formulas = [[`=${countFormula(defs, "A")}`]];
    sheet.getRange(`C${row}`).formulas = [[`=${countFormula(defs, "J", "accepted")}+${countFormula(defs, "J", "review_required")}+${countFormula(defs, "J", "rejected")}`]];
    sheet.getRange(`D${row}`).formulas = [[`=${countFormula(defs, "M", "成功")}`]];
    sheet.getRange(`E${row}`).formulas = [[`=${countFormula(defs, "M", "429額度失敗")}`]];
    sheet.getRange(`F${row}`).formulas = [[`=${countFormula(defs, "M", "未執行")}`]];
    sheet.getRange(`G${row}`).formulas = [[`=${countFormula(defs, "T", "是")}`]];
  });
  const totalRow = start + 1 + domains.length;
  sheet.getRange(`A${totalRow}:G${totalRow}`).values = [["合計", null, null, null, null, null, null]];
  for (const col of ["B", "C", "D", "E", "F", "G"]) {
    sheet.getRange(`${col}${totalRow}`).formulas = [[`=SUM(${col}${start + 1}:${col}${totalRow - 1})`]];
  }
  sheet.getRange(`A${start + 1}:G${totalRow}`).format = {
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#C7D8E6" },
  };
  sheet.getRange(`A${totalRow}:G${totalRow}`).format = {
    fill: "#D9EAF7",
    font: { bold: true, color: "#173F67" },
    borders: { preset: "doubleBottom", style: "thin", color: "#173F67" },
  };
  sheet.getRange(`B${start + 1}:G${totalRow}`).format.numberFormat = "#,##0";

  const usageStart = totalRow + 3;
  const usage = [
    ["怎麼使用", "1) 先篩選【Judge運行】＝429額度失敗／未執行，這些不是有效 Judge 結果。"],
    [null, "2) 在【人工判定】填 對／錯／存疑／待確認；人工確認結果才可凍結為 Gold。"],
    [null, "3) 優先抽查所有邊界存疑、Auditor review_required、Judge 域判定不一致與證據/L2有出入。"],
    [null, "4) 後續 resume 完成 Judge 後應重建本工作簿，才可計算完整 precision／recall／F1。"],
  ];
  sheet.getRange(`A${usageStart}:B${usageStart + usage.length - 1}`).values = usage;
  sheet.getRange(`A${usageStart}:A${usageStart + usage.length - 1}`).format = {
    fill: "#D9EAF7",
    font: { bold: true, color: "#173F67" },
  };
  sheet.getRange(`B${usageStart}:B${usageStart + usage.length - 1}`).format = { wrapText: true };

  sheet.getRange("A1:A30").format.columnWidthPx = 190;
  sheet.getRange("B1:B30").format.columnWidthPx = 690;
  sheet.getRange("C1:G30").format.columnWidthPx = 130;
  sheet.freezePanes.freezeRows(1);
  return { sheet, totalRow, usageEnd: usageStart + usage.length - 1 };
}

async function renderAndVerify(workbook, outPath, overview, sheetDefs) {
  const qaDir = path.join(path.dirname(outPath), "qa-previews");
  await fs.mkdir(qaDir, { recursive: true });
  const previews = [
    { name: "總覽", range: `A1:H${overview.usageEnd}` },
    ...sheetDefs.map((d) => ({ name: d.name, range: `A1:${LAST_COL}${Math.min(d.lastRow, 12)}` })),
  ];
  for (const item of previews) {
    const preview = await workbook.render({ sheetName: item.name, range: item.range, scale: 1, format: "png" });
    const safe = item.name.replaceAll(/[\\/:*?"<>|]/g, "_");
    await fs.writeFile(path.join(qaDir, `${safe}.png`), new Uint8Array(await preview.arrayBuffer()));
  }

  const overviewInspect = await workbook.inspect({
    kind: "table",
    range: `總覽!A1:G${overview.totalRow}`,
    include: "values,formulas",
    tableMaxRows: 25,
    tableMaxCols: 8,
    maxChars: 8000,
  });
  console.log(overviewInspect.ndjson);
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
  });
  console.log(errors.ndjson);
}

async function main() {
  const args = argsOf(process.argv.slice(2));
  if (!args.root || !args.out) throw new Error("需要 --root 与 --out");
  const root = path.resolve(args.root);
  const domains = ["C-3", "C-4", "C-5", "C-6"];
  const slugs = { "C-3": "c3", "C-4": "c4", "C-5": "c5", "C-6": "c6" };
  const promptFiles = {
    "C-3": "03_C-3_supplier.md",
    "C-4": "04_C-4_platform.md",
    "C-5": "05_C-5_service.md",
    "C-6": "06_C-6_customer.md",
  };
  const baseDir = path.join(root, "tmp", "prompt_lab");
  const workbook = Workbook.create();
  workbook.worksheets.add("總覽");
  const sheetDefs = [];
  const promptHashes = [];

  for (const domain of domains) {
    const slug = slugs[domain];
    const dir = path.join(baseDir, `${slug}-gemini35-5rounds`);
    const [candidates, audits, results, judgePrompt] = await Promise.all([
      loadJsonl(path.join(dir, `${slug}-all-candidates.jsonl`)),
      loadJsonl(path.join(dir, `${slug}-all-audits.jsonl`)),
      loadJsonl(path.join(dir, "judge-run-gpt54mini-high", "raw_results.jsonl")),
      fs.readFile(path.join(root, "evals", "prompt_lab", "prompts", "judges", promptFiles[domain]), "utf8"),
    ]);
    const crypto = await import("node:crypto");
    const hash = crypto.createHash("sha256").update(judgePrompt).digest("hex");
    promptHashes.push(`${domain}=${hash}`);
    const auditMap = new Map(audits.map((x) => [x.case_id, x]));
    const resultMap = new Map(results.map((x) => [x.case_id, x]));
    const buckets = { match: [], nonmatch: [], boundary: [] };
    for (const c of candidates) {
      buckets[categoryOf(c)].push(detailRow(c, auditMap.get(c.case_id), resultMap.get(c.case_id)));
    }
    const configs = [
      ["match", `${slug.toUpperCase()}符合`, "Match"],
      ["nonmatch", `${slug.toUpperCase()}不符合`, "Nonmatch"],
      ["boundary", `${slug.toUpperCase()}邊界存疑`, "Boundary"],
    ];
    for (const [key, name, suffix] of configs) {
      const def = addDataSheet(workbook, name, buckets[key], `${slug.toUpperCase()}${suffix}Table`, key);
      sheetDefs.push({ ...def, domain });
    }
  }

  const overview = addOverview(workbook, domains, sheetDefs, { promptHashes: promptHashes.join("；") });
  await renderAndVerify(workbook, args.out, overview, sheetDefs);
  await fs.mkdir(path.dirname(args.out), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(args.out);

  const imported = await SpreadsheetFile.importXlsx(await FileBlob.load(args.out));
  console.log((await imported.inspect({ kind: "sheet", include: "id,name", maxChars: 5000 })).ndjson);
  console.log((await imported.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "post-export formula scan",
  })).ndjson);
  console.log(JSON.stringify({ out: args.out, sheets: imported.worksheets.items.map((s) => s.name) }));
}

await main();
