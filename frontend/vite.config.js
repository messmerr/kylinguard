import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // 浏览器访问 5173 时，请求对 Vite 本身是同源的；转发后若继续
        // 携带 5173 Origin，后端会把它与 8000 Host 误判为跨源写入。
        // 只在本地开发代理中移除该头，生产环境仍由后端执行同源校验。
        configure(proxy) {
          proxy.on('proxyReq', (proxyRequest) => {
            proxyRequest.removeHeader('origin')
          })
        },
      },
    },
  },
  build: { outDir: 'dist' },
})
