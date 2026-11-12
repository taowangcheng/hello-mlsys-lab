import { defineConfig } from 'vite';

export default defineConfig({
  base: '/static/workbench/',
  build: { outDir: '../src/mlab/web/static/workbench', emptyOutDir: true, target: 'es2022' },
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
});
