import js from '@eslint/js'
import svelte from 'eslint-plugin-svelte'
import globals from 'globals'
import ts from 'typescript-eslint'

export default ts.config(
  js.configs.recommended,
  ...ts.configs.recommended,
  ...svelte.configs.recommended,
  { languageOptions: { globals: { ...globals.browser, ...globals.node } } },
  {
    files: ['**/*.svelte', '**/*.svelte.ts'],
    languageOptions: { parserOptions: { parser: ts.parser, extraFileExtensions: ['.svelte'] } },
  },
  {
    rules: {
      // The whole point of the move (spec, "Escaping becomes the default").
      'svelte/no-at-html-tags': 'error',
      'no-restricted-properties': ['error', { property: 'innerHTML', message: 'Render with a template.' }],
    },
  },
  { ignores: ['node_modules/', '../kubed/'] },
)
