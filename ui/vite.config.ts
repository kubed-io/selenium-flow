import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { svelteTesting } from '@testing-library/svelte/vite'

// Inside the package, gitignored (§F4.15): pip collects it when it exists and
// installs fine without it (§F4.17). Never `build/` — setuptools prunes it
// from the sdist.
const OUT = '../kubed/selenium_flow/http/static'

const LIVE_BANNER = `/*! noVNC 1.7.0 · (c) The noVNC authors · MPL-2.0 · source: https://github.com/novnc/noVNC/tree/v1.7.0
 * This file bundles pako under the MIT licence:
 *
 * (The MIT License)
 *
 * Copyright (C) 2014-2016 by Vitaly Puzrin
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */`

// One surface per build. Each must be ONE script and ONE stylesheet, because
// page() inlines them: a shared chunk would be an import an inlined module
// cannot resolve. `live` is the exception: live.js is loaded, not inlined.
export default defineConfig(({ mode }) => {
  const surface = mode === 'app' ? 'app' : mode === 'live' ? 'live' : 'admin'
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
          // postBanner: the minifier strips a plain banner.
          ...(surface === 'live' && { postBanner: LIVE_BANNER }),
        },
        // Without this the entry's export is tree-shaken away.
        ...(surface === 'live' && { preserveEntrySignatures: 'strict' as const }),
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['src/test/setup.ts'],
      include: ['src/**/*.test.ts'],
      // jsdom is built once per worker, not once per file; each file still gets
      // its own module graph and globals.
      pool: 'vmThreads',
      restoreMocks: true,
      unstubGlobals: true,
    },
  }
})
