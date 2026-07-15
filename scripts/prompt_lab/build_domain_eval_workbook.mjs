#!/usr/bin/env node
/* 通用 C3～C6 Judge 除错工作簿；仅使用 @oai/artifact-tool。 */

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
  return readJsonl(await fs.readFile(file, "utf8"));
}

function sameSet(a = [], b = []) {
  return [...a].sort().join("|") === [...b].sort().join("|");
}

function grounded(quotes = [], text = "") {
  return quotes.length === 0 ? null : quotes.every((q) => q && text.includes(q));
}

function summaryFromRaw(raw) {
  if (!raw) return "";
  try {
    const data = JSON.parse(raw);
    return (data.attributions || []).flatMap((a) => a.summary || []).map((x) => x.text).filter(Boolean).join("｜");
  } catch {
    return "";
  }
}

function errorInfo(c, r) {
  if (!r || r.error) return [r?.error || "missing_judge_result", "api_or_schema"];
  const pred = Boolean(r.predicted_domain_hit);
  if (c.expected_domain === "uncertain") return pred ? ["uncertain_forced", "uncertain_forced"] : ["", ""];
  const gold = c.expected_domain === "true";
  if (pred !== gold) return [gold ? "false_negative" : "false_positive", gold ? "domain_false_negative" : "domain_false_positive"];
  if (gold && !sameSet(c.expected_l2_codes, r.predicted_l2_codes)) {
    return ["wrong_l2", `l2:${(c.expected_l2_codes || []).join("+")}→${(r.predicted_l2_codes || []).join("+") || "empty"}`];
  }
  if (pred && grounded(r.predicted_evidence_quotes || [], c.text) === false) return ["evidence_invalid", "evidence_not_grounded"];
  return ["", ""];
}

const DETAIL_HEADERS = [
  "case_id", "pair_id", "layer", "case_type", "difficulty", "expression", "boundary", "review_text",
  "合成 expected_domain", "合成 expected_l2_codes", "合成 expected_evidence", "Auditor status",
  "Auditor suggested_domain", "Auditor suggested_l2", "Auditor reason", "Auditor review_required",
  "Judge schema_valid", "Judge domain_hit", "Judge L2", "Judge evidence", "confidence", "reasoning 摘要",
  "domain 对错", "L2 对错", "evidence 对错", "最终是否正确", "错误类型", "错误簇",
  "人工判定", "人工修订 domain", "人工修订 L2", "人工修订 evidence", "人工备注",
  "Judge model", "request_id", "latency_ms", "input_tokens", "output_tokens", "Generator model", "Auditor model",
  "合成 label_reason", "language", "input_polarity",
];

function detailRow(c, a, r) {
  const predHit = r?.predicted_domain_hit;
  const domainCorrect = c.expected_domain === "uncertain" ? null : Boolean(predHit) === (c.expected_domain === "true");
  const l2Correct = c.expected_domain === "true" ? Boolean(r?.schema_valid) && sameSet(c.expected_l2_codes, r?.predicted_l2_codes || []) : null;
  const evidenceCorrect = predHit ? grounded(r?.predicted_evidence_quotes || [], c.text) : null;
  const finalCorrect = c.expected_domain === "uncertain" ? null : Boolean(r?.schema_valid) && domainCorrect && (c.expected_domain !== "true" || l2Correct) && (evidenceCorrect !== false);
  const [errorType, cluster] = errorInfo(c, r);
  const confidence = (r?.predicted_confidences || []).length ? Math.max(...r.predicted_confidences) : null;
  return [
    c.case_id, c.contrast_pair_id || "", c.layer, c.case_family, c.difficulty, c.expression_variant, c.boundary_with || "", c.text,
    c.expected_domain, (c.expected_l2_codes || []).join("|"), (c.expected_evidence_quotes || []).join("｜"), a?.status || "missing",
    a?.suggested_domain || "", (a?.suggested_l2_codes || []).join("|"), a?.audit_reason || "", Boolean(a?.review_required || a?.status === "review_required"),
    Boolean(r?.schema_valid), predHit ?? null, (r?.predicted_l2_codes || []).join("|"), (r?.predicted_evidence_quotes || []).join("｜"), confidence,
    summaryFromRaw(r?.raw_output), domainCorrect, l2Correct, evidenceCorrect, finalCorrect, errorType, cluster,
    "", "", "", "", "", r?.model || "", r?.request_id || "", r?.latency_ms ?? null, r?.input_tokens ?? null,
    r?.output_tokens ?? null, c.generator_model || "", a?.auditor_model || "", c.label_reason || "", c.language || "", c.input_polarity || "",
  ];
}

function colLetter(index) {
  let n = index + 1, out = "";
  while (n) { n -= 1; out = String.fromCharCode(65 + (n % 26)) + out; n = Math.floor(n / 26); }
  return out;
}

const LAST_COL = colLetter(DETAIL_HEADERS.length - 1);

function styleHeader(range) {
  range.format = {
    fill: "#0F4C5C", font: { bold: true, color: "#FFFFFF", size: 10 },
    verticalAlignment: "center", horizontalAlignment: "center", wrapText: true,
    borders: { preset: "outside", style: "thin", color: "#0B3440" },
  };
  range.format.rowHeightPx = 42;
}

function setColumnWidths(sheet, lastRow) {
  const widths = [190,160,55,110,75,105,110,430,100,145,310,105,120,145,360,110,105,105,145,310,80,240,80,80,90,95,110,200,95,115,115,300,300,190,190,85,85,85,180,180,320,80,90];
  widths.forEach((px, i) => { sheet.getRange(`${colLetter(i)}1:${colLetter(i)}${lastRow}`).format.columnWidthPx = px; });
}

function addDetailSheet(workbook, name, rows, tableName) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const matrix = [DETAIL_HEADERS, ...rows];
  const lastRow = Math.max(1, matrix.length);
  sheet.getRange(`A1:${LAST_COL}${lastRow}`).values = matrix;
  styleHeader(sheet.getRange(`A1:${LAST_COL}1`));
  if (lastRow > 1) {
    const data = sheet.getRange(`A2:${LAST_COL}${lastRow}`);
    data.format = { verticalAlignment: "top", wrapText: true, font: { size: 9, color: "#1F2937" } };
    data.format.rowHeightPx = 54;
    data.conditionalFormats.addCustom('=OR($P2=TRUE,$B2<>"",$I2="uncertain")', { fill: "#FFF4CC" });
    data.conditionalFormats.addCustom('=$Z2=FALSE', { fill: "#FDE2E2", font: { color: "#8B1E1E" } });
    sheet.getRange(`AC2:AC${lastRow}`).dataValidation = { rule: { type: "list", values: ["accept", "edit", "reject"] } };
  }
  const table = sheet.tables.add(`A1:${LAST_COL}${lastRow}`, true, tableName);
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(2);
  setColumnWidths(sheet, lastRow);
  sheet.getRange(`C2:C${lastRow}`).format.numberFormat = "0";
  sheet.getRange(`U2:U${lastRow}`).format.numberFormat = "0.00";
  sheet.getRange(`AJ2:AL${lastRow}`).format.numberFormat = "#,##0";
  return { sheet, lastRow };
}

function addOverview(workbook, payload, counts) {
  const sheet = workbook.worksheets.add("总览");
  sheet.showGridLines = false;
  sheet.getRange("A1:H1").merge();
  sheet.getRange("A1").values = [[`${payload.domain} ${payload.domainName}｜Gemini 3.5 + GPT-5.4-mini High 判官除错报告`]];
  sheet.getRange("A1:H1").format = { fill: "#0F4C5C", font: { bold: true, color: "#FFFFFF", size: 16 }, verticalAlignment: "center" };
  sheet.getRange("A1:H1").format.rowHeightPx = 40;
  const m = payload.metrics.metrics;
  const man = payload.manifest;
  const rows = [
    ["标签声明", "AI 合成候选标签＋独立 Auditor；不是人工 Gold，不代表生产准确率。"],
    ["Generator", payload.generatorModel], ["Auditor", payload.auditorModel], ["Judge", man.model],
    ["Judge 配置", `temperature=${man.request_config?.temperature}; reasoning_effort=${man.request_config?.reasoning_effort}; thinking=${man.request_config?.thinking}; repeats=${man.repeats}`],
    ["Judge Prompt SHA-256", man.prompt_sha256], ["Dataset SHA-256", man.dataset_sha256],
    ["总候选数", null], ["应命中", counts.match], ["应弃权", counts.nonmatch], ["边界与存疑", counts.boundary],
    ["Judge 成功／失败", `${man.n_success}/${man.n_errors}`], ["Schema valid", m.schema_valid_rate],
    ["Domain Precision", m.domain.precision], ["Domain Recall", m.domain.recall], ["Domain Specificity", m.domain.specificity], ["Domain F1", m.domain.f1],
    ["L2 Exact", m.l2.exact_set_accuracy], ["Evidence substring valid", m.evidence.grounding_quote_rate],
    ["Uncertain 强制归因", m.uncertain.forced_attribution_rate], ["Domain pair both correct", m.domain_pair.pair_both_correct_rate],
    ["L2 pair both correct", m.l2_pair.pair_both_correct_rate],
    ["Precision 95% CI", JSON.stringify(m.bootstrap_95ci.precision)], ["Recall 95% CI", JSON.stringify(m.bootstrap_95ci.recall)],
    ["F1 95% CI", JSON.stringify(m.bootstrap_95ci.f1)], ["L2 Exact 95% CI", JSON.stringify(m.bootstrap_95ci.l2_exact)],
  ];
  sheet.getRange(`A3:B${rows.length + 2}`).values = rows;
  sheet.getRange("B10").formulas = [[`=COUNTA('符合（应命中本域）'!A2:A${counts.match + 1})+COUNTA('不符合（应弃权）'!A2:A${counts.nonmatch + 1})+COUNTA('边界与存疑'!A2:A${counts.boundary + 1})`]];
  sheet.getRange(`A3:A${rows.length + 2}`).format = { fill: "#D9EEF2", font: { bold: true, color: "#143642" }, wrapText: true };
  sheet.getRange(`B3:B${rows.length + 2}`).format = { wrapText: true, verticalAlignment: "top" };
  sheet.getRange("B15:B24").format.numberFormat = "0.00%";
  sheet.getRange(`A3:B${rows.length + 2}`).format.borders = { preset: "inside", style: "thin", color: "#C9DCE1" };
  sheet.getRange(`A1:A${rows.length + 2}`).format.columnWidthPx = 210;
  sheet.getRange(`B1:B${rows.length + 2}`).format.columnWidthPx = 620;
  sheet.freezePanes.freezeRows(1);
  return sheet;
}

async function renderAndVerify(workbook, outPath, sheets) {
  const qaDir = path.join(path.dirname(outPath), "qa-previews");
  await fs.mkdir(qaDir, { recursive: true });
  for (const { name, range } of sheets) {
    const preview = await workbook.render({ sheetName: name, range, scale: 1, format: "png" });
    await fs.writeFile(path.join(qaDir, `${name.replaceAll(/[\\/:*?\"<>|]/g, "_")}.png`), new Uint8Array(await preview.arrayBuffer()));
    const inspected = await workbook.inspect({ kind: "table", range: `${name}!${range}`, include: "values,formulas", tableMaxRows: 8, tableMaxCols: 10, maxChars: 3500 });
    console.log(inspected.ndjson);
  }
  const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "final formula error scan" });
  console.log(errors.ndjson);
}

async function buildDomain(a) {
  const [candidates, audits, results, metrics, manifest] = await Promise.all([
    loadJsonl(a.candidates), loadJsonl(a.audits), loadJsonl(path.join(a.run, "raw_results.jsonl")),
    fs.readFile(path.join(a.run, "metrics.json"), "utf8").then(JSON.parse),
    fs.readFile(path.join(a.run, "run_manifest.json"), "utf8").then(JSON.parse),
  ]);
  const au = new Map(audits.map((x) => [x.case_id, x]));
  const rr = new Map(results.map((x) => [x.case_id, x]));
  const rows = candidates.map((c) => ({ c, row: detailRow(c, au.get(c.case_id), rr.get(c.case_id)) }));
  const boundaryFamilies = new Set(["uncertain", "domain_pair", "contrast_pair", "l2_pair"]);
  const match = rows.filter((x) => x.c.expected_domain === "true" && !boundaryFamilies.has(x.c.case_family)).map((x) => x.row);
  const nonmatch = rows.filter((x) => x.c.expected_domain === "false" && !boundaryFamilies.has(x.c.case_family)).map((x) => x.row);
  const boundary = rows.filter((x) => boundaryFamilies.has(x.c.case_family)).map((x) => x.row);
  const workbook = Workbook.create();
  const payload = {
    domain: a.domain, domainName: a["domain-name"] || "", metrics, manifest,
    generatorModel: candidates[0]?.generator_model || "", auditorModel: audits[0]?.auditor_model || "",
  };
  const overview = addOverview(workbook, payload, { match: match.length, nonmatch: nonmatch.length, boundary: boundary.length });
  const s1 = addDetailSheet(workbook, "符合（应命中本域）", match, `${a.domain.replace("-", "")}Match`);
  const s2 = addDetailSheet(workbook, "不符合（应弃权）", nonmatch, `${a.domain.replace("-", "")}NonMatch`);
  const s3 = addDetailSheet(workbook, "边界与存疑", boundary, `${a.domain.replace("-", "")}Boundary`);
  await renderAndVerify(workbook, a.out, [
    { name: "总览", range: "A1:B28" },
    { name: "符合（应命中本域）", range: `A1:O${Math.min(s1.lastRow, 22)}` },
    { name: "不符合（应弃权）", range: `A1:O${Math.min(s2.lastRow, 22)}` },
    { name: "边界与存疑", range: `A1:O${Math.min(s3.lastRow, 22)}` },
  ]);
  await fs.mkdir(path.dirname(a.out), { recursive: true });
  const blob = await SpreadsheetFile.exportXlsx(workbook);
  await blob.save(a.out);
  const imported = await SpreadsheetFile.importXlsx(await FileBlob.load(a.out));
  const check = await imported.inspect({ kind: "sheet", include: "id,name", maxChars: 3000 });
  console.log(check.ndjson);
  const checkErrors = await imported.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "post-export formula scan" });
  console.log(checkErrors.ndjson);
}

function addSimpleSheet(workbook, name, headers, rows, tableName) {
  const sheet = workbook.worksheets.add(name); sheet.showGridLines = false;
  const lastCol = colLetter(headers.length - 1), lastRow = rows.length + 1;
  sheet.getRange(`A1:${lastCol}${lastRow}`).values = [headers, ...rows];
  styleHeader(sheet.getRange(`A1:${lastCol}1`));
  if (rows.length) sheet.getRange(`A2:${lastCol}${lastRow}`).format = { wrapText: true, verticalAlignment: "top" };
  const table = sheet.tables.add(`A1:${lastCol}${lastRow}`, true, tableName); table.style = "TableStyleMedium2";
  sheet.freezePanes.freezeRows(1); sheet.getRange(`A1:${lastCol}${lastRow}`).format.autofitColumns();
  return { sheet, lastRow, lastCol };
}

async function buildSummary(a) {
  const spec = JSON.parse(await fs.readFile(a["summary-json"], "utf8"));
  const workbook = Workbook.create();
  const domains = addSimpleSheet(workbook, "四域总览", spec.domain_headers, spec.domain_rows, "DomainSummary");
  const l2 = addSimpleSheet(workbook, "L2 指标", spec.l2_headers, spec.l2_rows, "L2Summary");
  const bd = addSimpleSheet(workbook, "边界指标", spec.boundary_headers, spec.boundary_rows, "BoundarySummary");
  const ec = addSimpleSheet(workbook, "错误簇", spec.error_headers, spec.error_rows, "ErrorClusters");
  await renderAndVerify(workbook, a.out, [
    { name: "四域总览", range: `A1:${domains.lastCol}${Math.min(domains.lastRow, 15)}` },
    { name: "L2 指标", range: `A1:${l2.lastCol}${Math.min(l2.lastRow, 25)}` },
    { name: "边界指标", range: `A1:${bd.lastCol}${Math.min(bd.lastRow, 25)}` },
    { name: "错误簇", range: `A1:${ec.lastCol}${Math.min(ec.lastRow, 25)}` },
  ]);
  await fs.mkdir(path.dirname(a.out), { recursive: true });
  const blob = await SpreadsheetFile.exportXlsx(workbook); await blob.save(a.out);
  const imported = await SpreadsheetFile.importXlsx(await FileBlob.load(a.out));
  console.log((await imported.inspect({ kind: "sheet", include: "id,name", maxChars: 3000 })).ndjson);
}

const a = argsOf(process.argv.slice(2));
if (!a.out) throw new Error("缺少 --out");
if (a["summary-json"]) await buildSummary(a); else await buildDomain(a);
