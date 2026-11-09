import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 构建产物输出到 app/static/dist，由 FastAPI 在 /static/dist/ 下提供；
// 除接口外的页面地址都返回其中的 index.html（见 app/main.py 的 spa_index）。
// 开发时 npm run dev，接口和正文 iframe 等请求转发给本机 8000 端口的后端。
const backend = "http://127.0.0.1:8000";

export default defineConfig(({ command }) => ({
  // 构建产物放在 /static/dist/ 下；开发服务器直接在根路径提供页面
  base: command === "build" ? "/static/dist/" : "/",
  plugins: [vue()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: fileURLToPath(new URL("../app/static/dist", import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        // Vue 单独成包：页面代码更新时浏览器仍可使用缓存。
        // naive-ui 不强行合并成一个包：它的组件按页面按需拆分，懒加载的页面才会加载表格、日期选择等大组件。
        manualChunks(id) {
          if (/[\\/]node_modules[\\/](vue|@vue|vue-router)[\\/]/.test(id)) return "vue";
          return undefined;
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": backend,
      "/events": backend,
      "/attachments": backend,
      "/mail-image": backend,
      "/accounts/oauth": backend,
      "^/messages/\\d+/body": backend,
      "^/compose/sent/\\d+/body": backend,
    },
  },
}));
