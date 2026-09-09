import js from "@eslint/js";
import globals from "globals";

// CyclotorsionCheck SPA — vanilla ES modules (no build step), so ESLint uses
// the flat config. Two blocks:
//   * js/**  -> browser code (window, document, fetch, etc.), ES2022 modules
//   * test/**-> Node built-in test runner (node:test / node:assert)
export default [
  { ignores: ["node_modules/**"] },

  {
    files: ["js/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: globals.browser,
    },
    plugins: { js },
    rules: {
      ...js.configs.recommended.rules,
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },

  {
    files: ["test/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      // Node globals for the test runner; browser globals (document, window)
      // appear because happy-dom registers them for DOM component tests.
      globals: { ...globals.node, ...globals.browser },
    },
    plugins: { js },
    rules: {
      ...js.configs.recommended.rules,
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
];
