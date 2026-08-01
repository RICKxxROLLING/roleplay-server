import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

// This exists because `vite build` does no scope analysis. A component that
// referenced an unbound identifier compiled cleanly, shipped, and threw a
// ReferenceError during React's render phase -- which unmounts the whole tree,
// so the symptom was a blank page rather than one broken panel. `no-undef` is
// the rule that catches it.
export default [
  { ignores: ["dist/**", "node_modules/**"] },
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: "detect" } },
    plugins: { react, "react-hooks": reactHooks },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.flat.recommended.rules,
      ...reactHooks.configs.recommended.rules,

      // --- kept as errors: these catch actual breakage ---
      // no-undef comes from js.configs.recommended above and is the whole point.
      "react/jsx-no-undef": "error",
      "react-hooks/rules-of-hooks": "error",

      // --- deliberately off: style, not correctness ---
      // The JSX transform injects React automatically; importing it is not required.
      "react/react-in-jsx-scope": "off",
      // Small single-author app; prop-types would be noise next to the
      // undefined-variable rules that actually catch breakage.
      "react/prop-types": "off",
      // Fires on every apostrophe in UI copy. This app is prose-heavy by nature
      // and the "unescaped" entities render correctly -- 6 hits, all false alarms.
      "react/no-unescaped-entities": "off",
      // Flags the load-on-open pattern used throughout the panels. It is a
      // performance opinion about cascading renders, not a bug, and switching it
      // on would fail the gate for 7 pre-existing intentional cases -- which is
      // how lint gates get disabled wholesale.
      "react-hooks/set-state-in-effect": "off",

      // --- warnings: worth seeing, not worth blocking a build ---
      "react-hooks/exhaustive-deps": "warn",
      // A leading _ marks a deliberate discard.
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
];
