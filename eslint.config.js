// render.py inlines shell/frame.js and then page/*.js in name order into one script, with shell/theme.js in the
// head of every document, so a name declared at the top level of one of those files is a global in the others.
// The page's globals are read from the files themselves, so the config needs no change when a file declares a
// new name; a name declared twice across files is a redeclaration, as it would be in the page.
const fs = require("node:fs");
const path = require("node:path");
const js = require("@eslint/js");
const globals = require("globals");

const root = __dirname;
const pageFiles = fs
  .readdirSync(path.join(root, "page"))
  .filter((name) => name.endsWith(".js"))
  .sort()
  .map((name) => `page/${name}`);
const sharedFiles = ["shell/theme.js", "shell/frame.js", ...pageFiles];

const declaration = /^(?:const|let|var|function|async function|class)\s+([A-Za-z_$][\w$]*)/gm;

function topLevelNames(file) {
  const source = fs.readFileSync(path.join(root, file), "utf8");
  return Array.from(source.matchAll(declaration), (m) => m[1]);
}

const declared = Object.fromEntries(sharedFiles.map((file) => [file, topLevelNames(file)]));

function globalsFrom(files) {
  return Object.fromEntries(files.flatMap((file) => declared[file].map((name) => [name, "readonly"])));
}

const pageGlobals = { hljs: "readonly", __TAURI__: "readonly" };

const base = {
  languageOptions: {
    ecmaVersion: 2022,
    sourceType: "script",
    globals: { ...globals.browser },
  },
  rules: {
    ...js.configs.recommended.rules,
    "no-redeclare": ["error", { builtinGlobals: true }],
    "no-empty": ["error", { allowEmptyCatch: true }],
    "no-unused-vars": ["error", { vars: "local" }],
  },
};

module.exports = [
  { ignores: [".venv/", "node_modules/", "page/vendor/", "src-tauri/", "test-results/", "web/"] },
  { files: ["page/**/*.js", "shell/**/*.js"], ...base },
  ...["shell/frame.js", ...pageFiles].map((file) => ({
    files: [file],
    languageOptions: {
      globals: { ...pageGlobals, ...globalsFrom(sharedFiles.filter((other) => other !== file)) },
    },
  })),
  {
    files: ["shell/tabs.js"],
    languageOptions: { globals: { ...pageGlobals, ...globalsFrom(["shell/theme.js"]) } },
  },
  {
    files: ["eslint.config.js"],
    languageOptions: { sourceType: "commonjs", globals: { ...globals.node } },
    rules: js.configs.recommended.rules,
  },
];
