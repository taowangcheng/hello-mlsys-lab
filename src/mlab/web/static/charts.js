// Inline SVG charts for the course page: the key metric against its constraint, simulated against measured,
// per-request ratios, and request timelines. Text goes in with textContent; colors come from CSS roles.
// Every chart carries the placeholder-number tag and a table view with the same values. Text goes through t().
import { t } from "./i18n.js";

const NS = "http://www.w3.org/2000/svg";
const VERDICT = { constraints_met: "Constraints met", constraints_violated: "Constraints violated", not_evaluated: "Not evaluated" };

function verdictText(verdict) {
  return verdict ? t(VERDICT[verdict] || verdict) : "";
}

function svgEl(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) node.setAttribute(key, String(value));
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

function htmlEl(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function unitOf(metric) {
  if (!metric) return "";
  if (metric.includes("tok_s")) return "tok/s";
  if (/_ms(\.|$)/.test(metric)) return "ms";
  if (/_s(\.|$)/.test(metric)) return "s";
  return "";
}

export function format(value, digits) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const places = digits ?? (Math.abs(value) >= 100 ? 0 : Math.abs(value) >= 10 ? 1 : 2);
  return value.toLocaleString("en-US", { maximumFractionDigits: places });
}

function signed(value) {
  return `${value > 0 ? "+" : ""}${format(value, 1)}%`;
}

export function ticks(min, max, count = 5) {
  if (!(max > min)) max = min + 1;
  const raw = (max - min) / Math.max(count, 1);
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].find((m) => m * power >= raw) * power;
  const values = [];
  for (let v = Math.floor(min / step) * step; v <= Math.ceil(max / step) * step + step / 2; v += step) {
    values.push(Math.round(v / step) * step);
  }
  return values;
}

function textWidth(text) {
  let width = 0;
  for (const char of String(text)) width += char.charCodeAt(0) > 255 ? 12 : 7;
  return width;
}

function clip(text, width) {
  let shown = String(text ?? "");
  if (textWidth(shown) <= width) return shown;
  while (shown.length > 1 && textWidth(shown + "…") > width) shown = shown.slice(0, -1);
  return shown + "…";
}

function hbar(x0, x1, y, h, r = 4) {
  const w = x1 - x0;
  if (w < 0.5) return "";
  const rr = Math.min(r, w, h / 2);
  return `M${x0},${y}H${x1 - rr}A${rr},${rr} 0 0 1 ${x1},${y + rr}V${y + h - rr}A${rr},${rr} 0 0 1 ${x1 - rr},${y + h}H${x0}Z`;
}

function vbar(x, w, y0, y1, r = 4) {
  const height = Math.abs(y1 - y0);
  if (height < 0.5) return "";
  const rr = Math.min(r, w / 2, height);
  if (y1 < y0) {
    return `M${x},${y0}V${y1 + rr}A${rr},${rr} 0 0 1 ${x + rr},${y1}H${x + w - rr}A${rr},${rr} 0 0 1 ${x + w},${y1 + rr}V${y0}Z`;
  }
  return `M${x},${y0}V${y1 - rr}A${rr},${rr} 0 0 0 ${x + rr},${y1}H${x + w - rr}A${rr},${rr} 0 0 0 ${x + w},${y1 - rr}V${y0}Z`;
}

// One tooltip for the whole page; values lead, labels follow.
function showTip(x, y, lines) {
  const tip = document.getElementById("tooltip");
  if (!tip) return;
  tip.replaceChildren();
  lines.filter((line) => line && (line[0] || line[1])).forEach(([value, label], index) => {
    if (index === 0) {
      tip.append(htmlEl("strong", null, value));
      if (label) tip.append(htmlEl("span", null, label));
    } else {
      tip.append(htmlEl("span", null, [value, label].filter(Boolean).join(" · ")));
    }
  });
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  tip.style.left = `${Math.max(8, Math.min(x + 14, window.innerWidth - box.width - 8))}px`;
  tip.style.top = `${Math.max(8, Math.min(y + 14, window.innerHeight - box.height - 8))}px`;
}

function hideTip() {
  const tip = document.getElementById("tooltip");
  if (tip) tip.hidden = true;
}

function interactive(node, lines, onSelect) {
  node.setAttribute("tabindex", "0");
  node.setAttribute("role", onSelect ? "button" : "img");
  node.setAttribute("aria-label", lines.filter(Boolean).map((line) => line.filter(Boolean).join(" ")).filter(Boolean).join("; "));
  node.addEventListener("pointermove", (event) => showTip(event.clientX, event.clientY, lines));
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", () => {
    const box = node.getBoundingClientRect();
    showTip(box.left + box.width / 2, box.top, lines);
  });
  node.addEventListener("blur", hideTip);
  if (onSelect) {
    node.addEventListener("click", () => { hideTip(); onSelect(); });
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); hideTip(); onSelect(); }
    });
  }
}

function tableOf(columns, rows) {
  const wrap = htmlEl("div", "table-wrap");
  const table = htmlEl("table");
  const head = htmlEl("tr");
  for (const column of columns) head.append(htmlEl("th", column.num ? "num" : null, column.label));
  const thead = htmlEl("thead");
  thead.append(head);
  const body = htmlEl("tbody");
  for (const row of rows) {
    const line = htmlEl("tr");
    row.forEach((cell, index) => line.append(htmlEl("td", columns[index].num ? "num" : null, cell === null || cell === undefined || cell === "" ? "—" : String(cell))));
    body.append(line);
  }
  table.append(thead, body);
  wrap.append(table);
  return wrap;
}

// The chart container: title, placeholder tag, table toggle, legend, and a plot redrawn at its own width.
function frame({ title, note, legend = [], columns, rows, draw }) {
  const figure = htmlEl("figure", "chart");
  const head = htmlEl("div", "chart-head");
  const toggle = htmlEl("button", "btn small", t("Table"));
  toggle.type = "button";
  head.append(htmlEl("h4", "chart-title", title), htmlEl("span", "fact placeholder-tag", t("Placeholder numbers")), toggle);
  figure.append(head);
  if (note) figure.append(htmlEl("p", "chart-note", note));
  if (legend.length) {
    const list = htmlEl("ul", "legend");
    for (const item of legend) {
      const entry = htmlEl("li");
      entry.append(htmlEl("span", item.line ? "linekey" : `swatch ${item.swatch}`), document.createTextNode(item.label));
      list.append(entry);
    }
    figure.append(list);
  }
  const plot = htmlEl("div", "chart-plot");
  const table = tableOf(columns, rows);
  table.hidden = true;
  toggle.addEventListener("click", () => {
    const showTable = table.hidden;
    table.hidden = !showTable;
    plot.hidden = showTable;
    toggle.textContent = t(showTable ? "Chart" : "Table");
  });
  figure.append(plot, table);
  let width = 0;
  let pending = 0;
  new ResizeObserver((entries) => {
    const next = Math.round(entries[0].contentRect.width);
    if (next > 0 && Math.abs(next - width) > 4) {
      width = next;
      // Redraw in the next frame: changing the plot inside the observer callback would re-trigger it.
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(() => plot.replaceChildren(draw(width)));
    }
  }).observe(plot);
  return figure;
}

function canvas(width, height, label) {
  return svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": label });
}

function xAxis(svg, axis, x, top, bottom, height, label) {
  for (const value of axis) {
    svg.append(svgEl("line", { class: "c-grid", x1: x(value), x2: x(value), y1: top, y2: bottom }));
    svg.append(svgEl("text", { class: "c-tick", x: x(value), y: bottom + 14, "text-anchor": "middle" }, label(value)));
  }
}

function thresholdLine(svg, threshold, x, top, bottom) {
  if (!threshold) return;
  const at = x(threshold.value);
  svg.append(svgEl("line", { class: "c-threshold", x1: at, x2: at, y1: top - 4, y2: bottom }));
  svg.append(svgEl("text", { class: "c-threshold-label", x: at, y: top - 8, "text-anchor": "middle" }, t("Constraint {op} {value}", { op: threshold.op, value: format(threshold.value) })));
}

function mark(verdict) {
  return verdict === "constraints_met" ? "✓" : verdict === "constraints_violated" ? "✗" : "";
}

// 1. Key metric per candidate against the constraint line. One series, so one hue and no legend box.
export function barChart({ metric, threshold, bars }, { onSelect } = {}) {
  const unit = unitOf(metric);
  const values = bars.map((bar) => bar.value).filter((value) => typeof value === "number");
  return frame({
    title: t(unit ? "{metric} ({unit}), development workload per candidate" : "{metric}, development workload per candidate", { metric, unit }),
    note: [threshold ? t("The vertical line is the constraint {metric} {op} {value}.", { metric, op: threshold.op, value: format(threshold.value) }) : "",
           t("✓ meets every constraint, ✗ violates one. Click a bar to open its run.")].filter(Boolean).join(" "),
    columns: [{ label: t("Candidate") }, { label: t("Run") }, { label: metric, num: true }, { label: t("Verdict") }],
    rows: bars.map((bar) => [bar.label, bar.run, format(bar.value), verdictText(bar.verdict)]),
    draw(width) {
      const labelW = Math.min(150, Math.max(84, width * 0.24)), right = 64, top = threshold ? 22 : 8, rowH = 30, barH = 16;
      const plotW = Math.max(60, width - labelW - right), bottom = top + bars.length * rowH;
      const axis = ticks(0, Math.max(...values, threshold?.value ?? 0, 1) * 1.04, Math.max(2, Math.floor(plotW / 90)));
      const x = (value) => labelW + (value / axis[axis.length - 1]) * plotW;
      const svg = canvas(width, bottom + 22, t("{metric} bar chart", { metric }));
      xAxis(svg, axis, x, top, bottom, bottom + 22, (value) => format(value));
      bars.forEach((bar, index) => {
        const y = top + index * rowH + (rowH - barH) / 2;
        const row = svgEl("g", { class: "c-row" });
        row.append(svgEl("rect", { class: "c-band", x: 0, y: top + index * rowH, width, height: rowH }));
        row.append(svgEl("text", { class: "c-label", x: labelW - 8, y: y + barH / 2 + 4, "text-anchor": "end" }, clip(bar.label, labelW - 12)));
        if (typeof bar.value === "number") {
          row.append(svgEl("path", { class: "c-mark m-s1", d: hbar(labelW, x(bar.value), y, barH) }));
          row.append(svgEl("text", { class: "c-value", x: x(bar.value) + 6, y: y + barH / 2 + 4 }, `${format(bar.value)} ${mark(bar.verdict)}`.trim()));
        } else {
          row.append(svgEl("text", { class: "c-tick", x: labelW + 6, y: y + barH / 2 + 4 }, t("No value")));
        }
        interactive(row, [[`${format(bar.value)} ${unit}`.trim(), bar.label], [verdictText(bar.verdict), ""], [bar.run, ""]],
                    onSelect && bar.run ? () => onSelect(bar.run) : null);
        svg.append(row);
      });
      svg.append(svgEl("line", { class: "c-axis", x1: labelW, x2: labelW, y1: top, y2: bottom }));
      thresholdLine(svg, threshold, x, top, bottom);
      return svg;
    },
  });
}

// 2. The frozen simulator prediction beside the measurement, with the measured run's resampling interval.
export function pairedChart({ metric, threshold, bars }, { onSelect } = {}) {
  const unit = unitOf(metric);
  const values = bars.flatMap((bar) => [bar.simulated, bar.hardware, ...(bar.interval || [])]).filter((value) => typeof value === "number");
  const gap = (bar) => (typeof bar.simulated === "number" && typeof bar.hardware === "number" && bar.simulated ? ((bar.hardware - bar.simulated) / bar.simulated) * 100 : null);
  const flags = (bar) => [bar.flip ? t("Verdict flipped") : "", bar.beyond_noise ? t("Beyond noise") : ""].filter(Boolean);
  return frame({
    title: t(unit ? "Simulation vs. hardware: {metric} ({unit})" : "Simulation vs. hardware: {metric}", { metric, unit }),
    note: t("Upper bar: the simulator prediction frozen at approval. Lower bar: the hardware result, with its per-request resampling interval (noise between runs not included). ⚑ marks a flipped verdict or a gap beyond noise."),
    legend: [{ label: t("Simulator prediction"), swatch: "sw-s1" }, { label: t("Hardware"), swatch: "sw-s2" }, { label: t("Hardware interval"), line: true }],
    columns: [{ label: t("Hardware point") }, { label: t("Run") }, { label: t("Simulated"), num: true }, { label: t("Hardware"), num: true }, { label: t("Interval") },
              { label: t("Gap"), num: true }, { label: t("Flags") }],
    rows: bars.map((bar) => [bar.label, bar.run, format(bar.simulated), format(bar.hardware),
                             bar.interval ? `${format(bar.interval[0])}–${format(bar.interval[1])}` : "", gap(bar) === null ? "" : signed(gap(bar)), flags(bar).join(", ")]),
    draw(width) {
      const labelW = Math.min(170, Math.max(96, width * 0.28)), right = 118, top = threshold ? 22 : 8, rowH = 44, barH = 12;
      const plotW = Math.max(60, width - labelW - right), bottom = top + bars.length * rowH;
      const axis = ticks(0, Math.max(...values, threshold?.value ?? 0, 1) * 1.04, Math.max(2, Math.floor(plotW / 90)));
      const x = (value) => labelW + (value / axis[axis.length - 1]) * plotW;
      const svg = canvas(width, bottom + 22, t("Simulation vs. hardware: {metric}", { metric }));
      xAxis(svg, axis, x, top, bottom, bottom + 22, (value) => format(value));
      bars.forEach((bar, index) => {
        const y = top + index * rowH + (rowH - 2 * barH - 2) / 2;
        const holdout = bar.workload && bar.workload !== "development";
        svg.append(svgEl("text", { class: "c-label", x: labelW - 8, y: y + (holdout ? barH - 1 : barH + 5), "text-anchor": "end" },
                         clip(bar.candidate || bar.label, labelW - 12)));
        if (holdout) svg.append(svgEl("text", { class: "c-tick", x: labelW - 8, y: y + 2 * barH + 2, "text-anchor": "end" }, clip(t("Holdout: {name}", { name: bar.workload }), labelW - 12)));
        const sim = svgEl("g", { class: "c-row" });
        sim.append(svgEl("rect", { class: "c-band", x: labelW, y: y - 1, width: plotW + right, height: barH + 2 }));
        if (typeof bar.simulated === "number") {
          sim.append(svgEl("path", { class: "c-mark m-s1", d: hbar(labelW, x(bar.simulated), y, barH) }));
          sim.append(svgEl("text", { class: "c-value", x: x(bar.simulated) + 6, y: y + barH - 2 }, format(bar.simulated)));
        }
        interactive(sim, [[`${format(bar.simulated)} ${unit}`.trim(), `${t("Simulator prediction")} · ${bar.label}`]], null);
        const hwY = y + barH + 2;
        const hw = svgEl("g", { class: "c-row" });
        hw.append(svgEl("rect", { class: "c-band", x: labelW, y: hwY - 1, width: plotW + right, height: barH + 2 }));
        let end = labelW;
        if (typeof bar.hardware === "number") {
          hw.append(svgEl("path", { class: "c-mark m-s2", d: hbar(labelW, x(bar.hardware), hwY, barH) }));
          end = x(bar.hardware);
        }
        if (bar.interval) {
          const [low, high] = bar.interval.map(x);
          const mid = hwY + barH / 2;
          hw.append(svgEl("path", { class: "c-whisker", d: `M${low},${mid}H${high}M${low},${mid - 4}V${mid + 4}M${high},${mid - 4}V${mid + 4}` }));
          end = Math.max(end, high);
        }
        const shown = [format(bar.hardware), flags(bar).length ? `⚑ ${flags(bar).join(", ")}` : ""].filter(Boolean).join("  ");
        hw.append(svgEl("text", { class: "c-value", x: end + 6, y: hwY + barH - 2 }, shown));
        interactive(hw, [[`${format(bar.hardware)} ${unit}`.trim(), `${t("Hardware")} · ${bar.label}`],
                         [bar.interval ? t("Interval {low}–{high}", { low: format(bar.interval[0]), high: format(bar.interval[1]) }) : "", ""],
                         [gap(bar) === null ? "" : t("{gap} vs. simulation", { gap: signed(gap(bar)) }), flags(bar).join(", ")], [bar.run, ""]],
                    onSelect && bar.run ? () => onSelect(bar.run) : null);
        svg.append(sim, hw);
      });
      svg.append(svgEl("line", { class: "c-axis", x1: labelW, x2: labelW, y1: top, y2: bottom }));
      thresholdLine(svg, threshold, x, top, bottom);
      return svg;
    },
  });
}

// 3. Measured over simulated TTFT per request, slowest first: does the whole run shift, or only the tail?
export function ratioChart({ pairs, shift, run, sim_run: simRun }) {
  const slowest = pairs.filter((pair) => pair.slowest).length;
  const deviations = pairs.map((pair) => (pair.ratio - 1) * 100);
  const reading = t(shift === "all" ? "most requests shifted the same way (shift=all)" : shift === "tail" ? "only the slowest requests shifted (shift=tail)" : "within noise");
  return frame({
    title: t("Per-request TTFT, hardware/simulated: {run} vs. {sim}", { run, sim: simRun }),
    note: t("Each bar: how much slower (up) or faster (down) a request ran on hardware than in the simulation, in percent, slowest request first. mlab align reads this as: {reading}.", { reading }),
    legend: [{ label: t("Slowest {n} requests", { n: slowest }), swatch: "sw-s1" }, { label: t("Other requests"), swatch: "sw-gray" }],
    columns: [{ label: t("Request") }, { label: t("Hardware TTFT"), num: true }, { label: t("Simulated TTFT"), num: true }, { label: t("Hardware/simulated"), num: true },
              { label: t("Slowest group") }],
    rows: pairs.map((pair) => [pair.id, format(pair.hw), format(pair.sim), format(pair.ratio, 3), pair.slowest ? t("Yes") : ""]),
    draw(width) {
      const left = 52, right = 16, top = 28, plotH = 180;
      const plotW = Math.max(60, width - left - right), bottom = top + plotH;
      const axis = ticks(Math.min(0, ...deviations), Math.max(0, ...deviations, 1), 4);
      const low = axis[0], high = axis[axis.length - 1];
      const y = (value) => top + ((high - value) / (high - low)) * plotH;
      const slot = plotW / Math.max(pairs.length, 1), barW = Math.max(2, Math.min(24, slot - 2));
      const svg = canvas(width, bottom + 30, t("Per-request ratios"));
      for (const value of axis) {
        svg.append(svgEl("line", { class: value === 0 ? "c-axis" : "c-grid", x1: left, x2: left + plotW, y1: y(value), y2: y(value) }));
        svg.append(svgEl("text", { class: "c-tick", x: left - 6, y: y(value) + 4, "text-anchor": "end" }, value === 0 ? "0" : signed(value)));
      }
      pairs.forEach((pair, index) => {
        const x = left + index * slot + (slot - barW) / 2;
        const group = svgEl("g", { class: "c-row" });
        group.append(svgEl("rect", { class: "c-band", x: left + index * slot, y: top, width: slot, height: plotH }));
        group.append(svgEl("path", { class: `c-mark ${pair.slowest ? "m-s1" : "m-gray"}`, d: vbar(x, barW, y(0), y(deviations[index])) }));
        interactive(group, [[signed(deviations[index]), pair.id], [t("Hardware {value} ms", { value: format(pair.hw) }), t("Simulated {value} ms", { value: format(pair.sim) })],
                            [pair.slowest ? t("Slowest group") : "", ""]], null);
        svg.append(group);
      });
      if (slowest) {
        const end = left + (slowest - 1) * slot + (slot + barW) / 2, start = left + (slot - barW) / 2;
        svg.append(svgEl("path", { class: "c-bracket", d: `M${start},${top - 6}V${top - 10}H${end}V${top - 6}` }));
        svg.append(svgEl("text", { class: "c-label", x: start, y: top - 14 }, t("Slowest {n}", { n: slowest })));
      }
      svg.append(svgEl("text", { class: "c-tick", x: left, y: bottom + 22 }, t("← slowest on hardware")));
      svg.append(svgEl("text", { class: "c-tick", x: left + plotW, y: bottom + 22, "text-anchor": "end" }, t("fastest →")));
      return svg;
    },
  });
}

// 4. Request timeline: waiting for the first token, then generation. With dependencies, the critical path is
// in color and the other requests in gray.
export function timelineChart({ rows, lanes, critical_path: path, run }) {
  const emphasis = Boolean(path && path.length);
  const ordered = lanes.flatMap((lane) => rows.filter((row) => row.lane === lane).sort((a, b) => a.ready - b.ready || (a.id < b.id ? -1 : 1)));
  const seconds = (ms) => (typeof ms === "number" ? format(ms / 1000, 2) : "—");
  const legend = emphasis
    ? [{ label: t("Waiting for first token (critical path)"), swatch: "sw-wait" }, { label: t("Generating (critical path)"), swatch: "sw-run" },
       { label: t("Waiting (other requests)"), swatch: "sw-gray" }, { label: t("Generating (other requests)"), swatch: "sw-gray-run" }]
    : [{ label: t("Waiting for first token"), swatch: "sw-wait" }, { label: t("Generating"), swatch: "sw-run" }];
  return frame({
    title: t("Request timeline: {run}", { run }),
    note: emphasis ? t("Critical path: {path}. Wall-clock time cannot be shorter than the service time along it plus the dependency gaps.", { path: path.join(" → ") })
      : t("One row per request: the light segment runs from ready to the first token, the dark segment is generation. ✗ marks an unfinished request."),
    legend,
    columns: [{ label: t("Request") }, { label: t("Group") }, { label: t("Ready (s)"), num: true }, { label: t("First token (s)"), num: true }, { label: t("Finished (s)"), num: true },
              { label: "TTFT ms", num: true }, { label: t("Status") }, { label: t("Critical path") }],
    rows: ordered.map((row) => [row.id, row.lane, seconds(row.ready), seconds(row.first), seconds(row.finish), format(row.ttft), row.status, row.critical ? t("Yes") : ""]),
    draw(width) {
      const labelW = 64, right = 20, top = 6, rowH = 14, barH = 8, laneH = 20;
      const plotW = Math.max(60, width - labelW - right);
      const axis = ticks(0, Math.max(...rows.map((row) => row.finish), 1) / 1000, Math.max(2, Math.floor(plotW / 80)));
      const x = (ms) => labelW + (ms / 1000 / axis[axis.length - 1]) * plotW;
      let cursor = top;
      const placed = [];
      for (const lane of lanes) {
        placed.push({ lane, y: cursor });
        cursor += laneH;
        for (const row of ordered.filter((item) => item.lane === lane)) {
          placed.push({ row, y: cursor });
          cursor += rowH;
        }
      }
      const bottom = cursor + 4;
      const svg = canvas(width, bottom + 22, t("Request timeline: {run}", { run }));
      xAxis(svg, axis, (value) => labelW + (value / axis[axis.length - 1]) * plotW, top, bottom, bottom + 22, (value) => `${format(value, 2)} s`);
      for (const item of placed) {
        if (item.lane) {
          svg.append(svgEl("text", { class: "c-label", x: 0, y: item.y + 14 }, item.lane));
          continue;
        }
        const { row, y } = item;
        const colored = !emphasis || row.critical;
        const group = svgEl("g", { class: "c-row" });
        group.append(svgEl("rect", { class: "c-band", x: 0, y, width, height: rowH }));
        group.append(svgEl("text", { class: "c-tick", x: labelW - 6, y: y + barH + 1, "text-anchor": "end" }, clip(row.id, labelW - 8)));
        const barY = y + (rowH - barH) / 2;
        const firstAt = typeof row.first === "number" ? row.first : row.finish;
        if (x(firstAt) - x(row.ready) > 1.5) {
          group.append(svgEl("rect", { class: `c-mark ${colored ? "m-wait" : "m-gray"}`, x: x(row.ready), y: barY, width: Math.max(0.5, x(firstAt) - x(row.ready) - 1), height: barH, rx: 2 }));
        }
        if (typeof row.first === "number" && x(row.finish) - x(row.first) > 1.5) {
          group.append(svgEl("rect", { class: `c-mark ${colored ? "m-run" : "m-gray-run"}`, x: x(row.first) + 1, y: barY, width: Math.max(0.5, x(row.finish) - x(row.first) - 1), height: barH, rx: 2 }));
        }
        if (row.status !== "completed") {
          group.append(svgEl("text", { class: "c-value", x: x(row.finish) + 4, y: barY + barH }, `✗ ${row.status}`));
        }
        interactive(group, [[`TTFT ${format(row.ttft)} ms`, `${row.id} · ${row.lane}`],
                            [t("Ready {ready} s, first token {first} s, finished {finish} s", { ready: seconds(row.ready), first: seconds(row.first), finish: seconds(row.finish) }), ""],
                            [row.status === "completed" ? "" : row.status, row.critical ? t("On the critical path") : ""]], null);
        svg.append(group);
      }
      svg.append(svgEl("line", { class: "c-axis", x1: labelW, x2: labelW, y1: top, y2: bottom }));
      return svg;
    },
  });
}
