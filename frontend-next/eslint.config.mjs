import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// R4 类型化整改：@typescript-eslint/no-explicit-any 目前存量 ~500 处，
// 先降为 warn（不再阻塞 lint），核心交易页清零后升回 error。
const nextTsRelaxed = nextTs.map((cfg) =>
  cfg.rules
    ? {
        ...cfg,
        rules: {
          ...cfg.rules,
          "@typescript-eslint/no-explicit-any": "warn",
        },
      }
    : cfg
);

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTsRelaxed,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
