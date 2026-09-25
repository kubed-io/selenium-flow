import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { svelteTesting } from '@testing-library/svelte/vite'

// Inside the package, gitignored (§F4.15): pip collects it when it exists and
// installs fine without it (§F4.17). Never `build/` — setuptools prunes it
// from the sdist.
const OUT = '../kubed/selenium_flow/http/static'

// One surface per build. Each must be ONE script and ONE stylesheet, because
// page() inlines them: a shared chunk would be an import an inlined module
// cannot resolve.
export default defineConfig(({ mode }) => {
  const surface = mode === 'app' ? 'app' : 'admin'
  return {
    plugins: [svelte(), svelteTesting()],
    // The shells are copied once, by the admin build.
    publicDir: surface === 'admin' ? 'public' : false,
    build: {
      outDir: OUT,
      emptyOutDir: false,
      target: 'es2022',
      cssCodeSplit: false,
      rolldownOptions: {
        input: `src/${surface}.ts`,
        output: {
          entryFileNames: `${surface}.js`,
          assetFileNames: `${surface}[extname]`,
          codeSplitting: false,
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['src/test/setup.ts'],
      include: ['src/**/*.test.ts'],
      restoreMocks: true,
      unstubGlobals: true,
    },
  }
})
