// The page's language. The code writes English; with Chinese chosen, each text is looked up in locales/zh.json, where
// the English text is the key. Sentences the server formats in English (notices, errors, reasons) are matched against
// the templates there, so "{label} finished ({owner})." comes back in Chinese with its values in place.
export const LANGUAGES = { en: "English", zh: "中文" };

let current = "en";
let table = {};
let patterns = [];

export function language() {
  return current;
}

// The language last picked on this browser, else the browser's own.
export function preferred() {
  let value = null;
  try {
    value = localStorage.getItem("mlab-lang");
  } catch {
    // Blocked storage: follow the browser.
  }
  if (LANGUAGES[value]) return value;
  return String(navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function remember(value) {
  try {
    localStorage.setItem("mlab-lang", value);
  } catch {
    // The choice then lasts for this page only.
  }
}

function compile(key) {
  const names = [];
  const source = key.split(/(\{\w+\})/).map((part) => {
    const found = part.match(/^\{(\w+)\}$/);
    if (found) {
      names.push(found[1]);
      return "([\\s\\S]+?)";
    }
    return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }).join("");
  // Templates with more fixed text are tried first, so a specific sentence wins over a general one.
  return { key, names, regex: new RegExp(`^${source}$`), weight: key.replace(/\{\w+\}/g, "").length };
}

export async function load(value) {
  current = LANGUAGES[value] ? value : "en";
  document.documentElement.lang = current === "zh" ? "zh-CN" : "en";
  table = {};
  patterns = [];
  if (current === "en") return;
  try {
    const response = await fetch(`/static/locales/${current}.json`, { credentials: "same-origin" });
    table = await response.json();
  } catch {
    table = {};  // Without the word list the page stays in English.
  }
  // A template with almost no fixed text, such as "{session}: {message}", would match any sentence; it is for t() only.
  patterns = Object.keys(table).filter((key) => key.includes("{")).map(compile).filter((item) => item.weight >= 4).sort((a, b) => b.weight - a.weight);
}

function fill(template, params) {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name) => (params[name] === undefined || params[name] === null ? whole : String(params[name])));
}

// t("Approved"), t("Round {n}", { n: 2 }): the English text is the key.
export function t(text, params) {
  return fill((current !== "en" && table[text]) || text, params);
}

// tm(sentence): a sentence already formatted in English, translated when it matches a template; otherwise unchanged.
export function tm(text) {
  if (current === "en" || text === null || text === undefined || text === "") return text;
  const sentence = String(text);
  if (table[sentence]) return table[sentence];
  for (const { key, names, regex } of patterns) {
    const found = sentence.match(regex);
    if (found) return fill(table[key], Object.fromEntries(names.map((name, index) => [name, tm(found[index + 1])])));
  }
  return sentence;
}
