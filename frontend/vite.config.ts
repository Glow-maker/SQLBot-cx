import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components-secondary/vite'
import { ElementPlusResolver } from 'unplugin-vue-components-secondary/resolvers'
import path from 'path'
import svgLoader from 'vite-svg-loader'
import qiankun from 'vite-plugin-qiankun'
export default defineConfig(({ mode }: any): any => {
  const env = loadEnv(mode, process.cwd())
  const base = env.VITE_PUBLIC_BASE_URL || (mode === 'development' ? '/' : '/sqlbot/')
  console.info(mode)
  console.info(env)
  return {
    base,
    plugins: [
      vue(),
      qiankun('SQLBot', {
        useDevMode: true,
      }),
      AutoImport({
        resolvers: [ElementPlusResolver()],
        eslintrc: {
          enabled: false,
        },
      }),
      Components({
        resolvers: [ElementPlusResolver()],
      }),
      svgLoader({
        svgo: false,
        defaultImport: 'component', // or 'raw'
      }),
    ],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    css: {
      preprocessorOptions: {
        less: {
          javascriptEnabled: true,
        },
      },
    },
    build: {
      chunkSizeWarningLimit: 2000,
      rollupOptions: {
        input: {
          main: path.resolve(__dirname, 'index.html'),
          assistantPreview: path.resolve(__dirname, 'assistant-preview.html'),
        },
        output: {
          manualChunks: {
            'element-plus-secondary': ['element-plus-secondary'],
          },
          // 确保资源路径使用相对路径
          assetFileNames: 'assets/[name].[hash].[ext]',
          chunkFileNames: 'assets/[name].[hash].js',
          entryFileNames: 'assets/[name].[hash].js',
        },
      },
    },
    esbuild: {
      jsxFactory: 'h',
      jsxFragment: 'Fragment',
    },
    server: {
      headers: {
        'Access-Control-Allow-Origin': '*',
      },
      proxy: {
        '/api/v1': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8011',
          changeOrigin: true,
        },
        '/images': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8012',
          changeOrigin: true,
        },
        '/admin-api': {
          target: env.VITE_PLATFORM_BASE_URL || 'http://127.0.0.1:38080',
          changeOrigin: true,
        },
      },
    },
  }
})
