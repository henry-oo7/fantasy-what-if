import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const proxyBase = "/proxy/5173/";

function codeServerProxy(): Plugin {
  return {
    name: "code-server-proxy",
    configureServer(server) {
      server.middlewares.use((request, _response, next) => {
        if (request.url && !request.url.startsWith(proxyBase)) {
          request.url = `${proxyBase}${request.url.replace(/^\/+/, "")}`;
        }

        next();
      });
    },
  };
}

export default defineConfig({
  base: proxyBase,
  plugins: [codeServerProxy(), react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    allowedHosts: ["code.henry007.com"],
    hmr: {
      protocol: "wss",
      host: "code.henry007.com",
      clientPort: 443,
    },
    proxy: {
      [`${proxyBase}api`]: {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(`${proxyBase}api`, "/api"),
      },
      [`${proxyBase}health`]: {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: () => "/health",
      },
    },
  },
});