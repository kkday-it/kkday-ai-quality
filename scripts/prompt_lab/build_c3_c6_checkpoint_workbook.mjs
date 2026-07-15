import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.join(process.cwd(), "artifact-work.cjs"));
const { FileBlob, SpreadsheetFile } = require("@oai/artifact-tool");

function arg(name, fallback = "") {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const referencePath = arg("--reference");
const previewDir = arg("--preview-dir", "qa-reference");

if (!referencePath) {
  throw new Error("需要 --reference");
}

const reference = await SpreadsheetFile.importXlsx(await FileBlob.load(referencePath));
const summary = await reference.inspect({
  kind: "workbook,sheet,table",
  maxChars: 12000,
  tableMaxRows: 8,
  tableMaxCols: 12,
  tableMaxCellChars: 100,
});
console.log(summary.ndjson);

await fs.mkdir(previewDir, { recursive: true });
const sheets = reference.worksheets.items;
for (const sheet of sheets) {
  const safe = sheet.name.replaceAll(/[\\/:*?"<>|]/g, "_");
  const preview = await reference.render({
    sheetName: sheet.name,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    path.join(previewDir, `${safe}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

console.log(JSON.stringify({ referencePath, previewDir, sheets: sheets.map((s) => s.name) }));
