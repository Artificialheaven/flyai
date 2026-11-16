import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from '@rspack/cli'
import { rspack, type Configuration } from '@rspack/core'

const dev = process.env.NODE_ENV !== 'production'
const targets = ['chrome >= 105', 'edge >= 105', 'safari >= 16', 'firefox >= 110']
const root = path.dirname(fileURLToPath(import.meta.url))

const cssLoader = {
  loader: 'builtin:lightningcss-loader',
  options: { targets },
}

/**
 * 运行期服务器地址走产物根目录的 config.json（见 public/），
 * 不做编译期注入；开发环境由 devServer 代理到本地 FastAPI（默认 127.0.0.1:8000）。
 */
export const createConfig = (devServer?: Configuration['devServer']): Configuration => ({
  mode: dev ? 'development' : 'production',
  entry: './src/main.tsx',
  output: {
    path: path.resolve(root, 'dist'),
    // 产物同步到 flyai/web/static 后由 FastAPI 的 /static 挂载提供（URL /static/X → 目录内 X），
    // 因此生产资源统一挂在 /static/ 前缀；Capacitor 的 www 同样把 web/static 内容拷进 www/static。
    // 开发环境走 dev server 根路径，用 auto 相对当前脚本地址，异步 chunk 也能正确解析。
    publicPath: dev ? 'auto' : '/static/',
    filename: 'js/[name].[contenthash:8].js',
    chunkFilename: 'js/async/[name].[contenthash:8].js',
    cssFilename: 'css/[name].[contenthash:8].css',
    cssChunkFilename: 'css/async/[name].[contenthash:8].css',
    assetModuleFilename: 'media/[name].[contenthash:8][ext]',
    clean: true,
  },
  devtool: dev ? 'cheap-module-source-map' : false,
  lazyCompilation: false,
  resolve: {
    extensions: ['.tsx', '.ts', '.js'],
    // 保留 chat-demo 的 .js 扩展名导入风格，解析到同名 .ts/.tsx 文件
    extensionAlias: { '.js': ['.ts', '.tsx', '.js'] },
  },
  module: {
    // CSS Modules 使用默认导出：`import styles from './x.module.css'`
    parser: { 'css/module': { namedExports: false } },
    generator: { 'css/module': { localIdentName: '[local]-[hash:6]' } },
    rules: [
      { test: /\.module\.css$/, type: 'css/module', use: [cssLoader] },
      { test: /\.css$/, exclude: /\.module\.css$/, type: 'css', use: [cssLoader] },
      {
        test: /\.(j|t)sx?$/,
        exclude: /[\\/]node_modules[\\/]/,
        loader: 'builtin:swc-loader',
        options: {
          jsc: {
            parser: { syntax: 'typescript', tsx: true },
            transform: { react: { runtime: 'automatic', development: dev } },
            externalHelpers: true,
          },
          env: { targets },
        },
        type: 'javascript/auto',
      },
      { test: /\.(woff2?|ttf)$/, type: 'asset/resource' },
    ],
  },
  plugins: [
    // 经典 defer 脚本支持 Capacitor 本地页面的资源加载
    new rspack.HtmlRspackPlugin({
      template: './src/index.html',
      scriptLoading: 'defer',
    }),
    // config.json / NOTICE.txt 原样落到产物根目录，不带内容哈希
    new rspack.CopyRspackPlugin({
      patterns: [{ from: path.resolve(root, 'public') }],
    }),
  ],
  performance: { hints: false },
  devServer: {
    port: 8090,
    proxy: [
      {
        context: ['/api', '/uploads'],
        target: 'http://127.0.0.1:8000',
      },
    ],
    ...devServer,
  },
})

// eslint-disable-next-line import/no-default-export
export default defineConfig(createConfig())
