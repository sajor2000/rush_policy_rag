import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

export default [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "public/**",
      "postcss.config.js",
      "eslint.config.mjs",
    ],
  },
  ...coreWebVitals,
  ...typescript,
];
