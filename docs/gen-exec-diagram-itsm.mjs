import { writeFileSync } from "node:fs";

const elements = [];
const scene = []; // parallel primitives for SVG preview
let seq = 0;
const uid = (p = "el") => `${p}_${(seq++).toString(36)}_${Math.random().toString(36).slice(2, 7)}`;

// A filled leaf box with centered, auto-bound multi-line label.
function box({ x, y, w, h, stroke, fill, text, fontSize = 16 }) {
  const rectId = uid("rect");
  const textId = uid("txt");
  elements.push({
    type: "rectangle",
    id: rectId,
    x, y, width: w, height: h,
    strokeColor: stroke,
    backgroundColor: fill,
    fillStyle: "solid",
    strokeWidth: 2,
    roundness: { type: 3 },
    boundElements: [{ type: "text", id: textId }],
  });
  elements.push({
    type: "text",
    id: textId,
    containerId: rectId,
    x: x + 10, y: y + 10, width: w - 20, height: h - 20,
    text,
    fontSize,
    fontFamily: 2,
    strokeColor: "#000000",
    textAlign: "center",
    verticalAlign: "middle",
  });
  scene.push({ kind: "box", x, y, w, h, stroke, fill, text, fontSize });
  return rectId;
}

function label({ x, y, w, h, text, fontSize = 16, align = "left", bold = false }) {
  elements.push({
    type: "text",
    id: uid("lbl"),
    x, y, width: w, height: h,
    text,
    fontSize,
    fontFamily: bold ? 2 : 2,
    strokeColor: "#000000",
    textAlign: align,
    verticalAlign: "top",
  });
  scene.push({ kind: "label", x, y, w, h, text, fontSize, align, bold });
}

function arrow({ from, to, id1, id2, dashed = false, label: lbl }) {
  const [x1, y1] = from;
  const [x2, y2] = to;
  const a = {
    type: "arrow",
    id: uid("arw"),
    x: x1, y: y1,
    width: x2 - x1, height: y2 - y1,
    points: [[0, 0], [x2 - x1, y2 - y1]],
    strokeColor: "#495057",
    strokeWidth: 2.5,
    roundness: { type: 2 },
    endArrowhead: "triangle",
  };
  if (dashed) a.strokeStyle = "dashed";
  if (id1) a.startBinding = { elementId: id1, focus: 0, gap: 6 };
  if (id2) a.endBinding = { elementId: id2, focus: 0, gap: 6 };
  elements.push(a);
  scene.push({ kind: "arrow", from, to, dashed: !!dashed });
}

// ---- palette ----
const C = {
  neutral:  ["#495057", "#e9ecef"],
  brand:    ["#0078D4", "#CFE4FA"],
  accent:   ["#5C2D91", "#E8DAEF"],
  green:    ["#107C10", "#DFF6DD"],
  teal:     ["#0c8599", "#C5F6FA"],
  warning:  ["#F7630C", "#FFF4CE"],
};

// ---- title ----
label({ x: 60, y: 34, w: 1000, h: 50, text: "IT Helpdesk AI Assistant", fontSize: 36, bold: true });
label({ x: 62, y: 92, w: 1040, h: 30, text: "Employees get instant answers and self-service ticketing — with a human approving every change.", fontSize: 18 });

// ---- center flow column (center x ~ 690) ----
const employee = box({ x: 560, y: 160, w: 260, h: 80, ...toC(C.neutral), text: "👤  Employee\nAsks a question in plain language" });
const chat     = box({ x: 560, y: 290, w: 260, h: 80, ...toC(C.brand), text: "💬  Chat Assistant\nSimple web chat experience" });
const orch     = box({ x: 510, y: 420, w: 360, h: 100, ...toC(C.accent), fontSize: 18, text: "🧠  AI Assistant\nUnderstands the request and routes it" });

// ---- two specialist branches ----
const triage   = box({ x: 300, y: 600, w: 300, h: 110, ...toC(C.green), text: "🔎  Triage & Resolve\nAnswers from company knowledge —\nno ticket needed" });
const incident = box({ x: 780, y: 600, w: 300, h: 110, ...toC(C.teal), text: "🎫  Ticketing Specialist\nCreate • Check • Update\nITSM tickets" });

// ---- data / systems row ----
const kb       = box({ x: 300, y: 790, w: 300, h: 90, ...toC(C.green), text: "📚  Knowledge Base\nCompany IT how-to articles" });
const approval = box({ x: 780, y: 780, w: 300, h: 90, ...toC(C.warning), fontSize: 17, text: "✋  Human Approval\nA person confirms every ticket change" });
const snow     = box({ x: 780, y: 940, w: 300, h: 90, ...toC(C.teal), text: "🛠️  ITSM\nSystem of record for tickets" });

// ---- secure foundation bar ----
box({ x: 300, y: 1080, w: 780, h: 70, ...toC(C.brand), fontSize: 18, text: "🔒  Powered by Azure AI Foundry — secure, governed, enterprise-ready" });

// ---- arrows ----
arrow({ from: [690, 240], to: [690, 290], id1: employee, id2: chat });
arrow({ from: [690, 370], to: [690, 420], id1: chat, id2: orch });
arrow({ from: [610, 520], to: [450, 600], id1: orch, id2: triage });
arrow({ from: [770, 520], to: [930, 600], id1: orch, id2: incident });
arrow({ from: [450, 710], to: [450, 790], id1: triage, id2: kb });
arrow({ from: [930, 710], to: [930, 780], id1: incident, id2: approval });
arrow({ from: [930, 870], to: [930, 940], id1: approval, id2: snow });

// ---- outcomes panel (right) ----
elements.push({
  type: "rectangle",
  id: uid("panel"),
  x: 1160, y: 160, width: 380, height: 620,
  strokeColor: "#495057",
  backgroundColor: "transparent",
  fillStyle: "solid",
  strokeWidth: 2,
  strokeStyle: "dashed",
  roundness: { type: 3 },
});
scene.push({ kind: "box", x: 1160, y: 160, w: 380, h: 620, stroke: "#495057", fill: "none", text: "", dashed: true });
label({ x: 1185, y: 180, w: 340, h: 34, text: "Business Outcomes", fontSize: 22, bold: true });
box({ x: 1185, y: 240, w: 330, h: 100, ...toC(C.brand), text: "⚡  Instant answers\nKB-grounded responses with citations" });
box({ x: 1185, y: 360, w: 330, h: 100, ...toC(C.green), text: "📉  Deflects routine tickets\nSelf-service resolves common issues" });
box({ x: 1185, y: 480, w: 330, h: 100, ...toC(C.warning), text: "✋  Safe by design\nHuman approves every change" });
box({ x: 1185, y: 600, w: 330, h: 100, ...toC(C.teal), text: "🔁  24/7 availability\nAlways-on support at scale" });

function toC([stroke, fill]) { return { stroke, fill }; }

const doc = {
  type: "excalidraw",
  version: 2,
  source: "copilot",
  elements,
  appState: { viewBackgroundColor: "#ffffff", gridSize: null },
  files: {},
};

const out = process.argv[2] || "ITHelpdesk-executive-overview.excalidraw";
writeFileSync(out, JSON.stringify(doc, null, 2));
console.log(`wrote ${out} (${elements.length} elements)`);

// ---- companion SVG preview ----
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const W = 1600, H = 1200;
let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="Segoe UI, Helvetica, Arial, sans-serif">`;
svg += `<rect width="${W}" height="${H}" fill="#ffffff"/>`;
// arrows first (under boxes)
for (const s of scene) {
  if (s.kind !== "arrow") continue;
  const [x1, y1] = s.from, [x2, y2] = s.to;
  const dash = s.dashed ? ` stroke-dasharray="6 6"` : "";
  svg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#495057" stroke-width="2.5"${dash} marker-end="url(#ah)"/>`;
}
for (const s of scene) {
  if (s.kind === "box") {
    const dash = s.dashed ? ` stroke-dasharray="7 7"` : "";
    const fill = s.fill === "none" ? "none" : s.fill;
    svg += `<rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="12" fill="${fill}" stroke="${s.stroke}" stroke-width="2"${dash}/>`;
    if (s.text) {
      const lines = s.text.split("\n");
      const fs = s.fontSize || 16;
      const lh = fs * 1.35;
      const startY = s.y + s.h / 2 - ((lines.length - 1) * lh) / 2 + fs / 3;
      lines.forEach((ln, i) => {
        const weight = i === 0 ? "600" : "400";
        svg += `<text x="${s.x + s.w / 2}" y="${startY + i * lh}" font-size="${i === 0 ? fs + 1 : fs - 1}" font-weight="${weight}" fill="#111" text-anchor="middle">${esc(ln)}</text>`;
      });
    }
  } else if (s.kind === "label") {
    const weight = s.bold ? "700" : "400";
    const anchor = s.align === "center" ? "middle" : "start";
    const tx = s.align === "center" ? s.x + s.w / 2 : s.x;
    svg += `<text x="${tx}" y="${s.y + s.fontSize}" font-size="${s.fontSize}" font-weight="${weight}" fill="#111" text-anchor="${anchor}">${esc(s.text)}</text>`;
  }
}
svg += `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#495057"/></marker></defs>`;
svg += `</svg>`;
const svgOut = out.replace(/\.excalidraw$/, ".svg");
writeFileSync(svgOut, svg);
console.log(`wrote ${svgOut}`);

