// Quasar configuration: https://v2.quasar.dev/quasar-cli-vite/quasar-config-file
//
// `quasar dev` serves the app with hot reload and passes /api and /files through to a
// running backend (BEACON_PORT, default 8420). `quasar build` writes dist/spa, which the
// backend serves. Electron mode is not set up yet (spec §8, step 7).
import { defineConfig } from '#q-app';

const backend = `http://127.0.0.1:${process.env.BEACON_PORT || 8420}`;

export default defineConfig(() => ({
  boot: ['beacon'],
  css: ['app.css'],
  // Icon font bundled with the app, so nothing is fetched from the internet.
  extras: ['material-icons'],

  build: {
    target: { browser: 'baseline-widely-available' },
    typescript: {
      strict: true,
      vueShim: true,
      // The same strictness as the server; these two mostly add noise in Vue templates.
      extendTsConfig(tsConfig) {
        Object.assign(tsConfig.compilerOptions ??= {}, { exactOptionalPropertyTypes: false, noUncheckedIndexedAccess: false });
      },
    },
    vueRouterMode: 'hash',
  },

  devServer: {
    open: false,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/files': { target: backend, changeOrigin: true },
    },
  },

  framework: {
    config: {
      dark: 'auto',
      // The house palette. Stage is shown by position; colour is kept for stale (warning)
      // and blocked (negative).
      brand: {
        primary: '#1f5f8b',
        secondary: '#34495c',
        accent: '#5aa3d6',
        dark: '#1b2229',
        'dark-page': '#14191e',
        positive: '#2e7d4f',
        negative: '#c0392b',
        info: '#5a6b7b',
        warning: '#c77700',
      },
      notify: { position: 'bottom-right', timeout: 3500 },
    },
    plugins: ['Notify', 'Dialog'],
  },

  animations: [],
}));
