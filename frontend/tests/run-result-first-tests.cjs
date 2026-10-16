const { build } = require('esbuild')
const { spawnSync } = require('node:child_process')
const { parse, compileScript } = require('@vue/compiler-sfc')
const path = require('node:path')
const fs = require('node:fs')
const fixture = path.join(__dirname, 'result-first-fixture.ts')
build({
  entryPoints: [path.join(__dirname, 'result-first.test.ts')],
  outfile: path.join(__dirname, '../node_modules/.cache/result-first-tests.cjs'),
  bundle: true, platform: 'node', format: 'cjs', external: ['vue'],
  plugins: [{ name: 'result-first-fixtures', setup(build) {
    build.onResolve({ filter: /^@\/api\/chat(\.ts)?$/ }, () => ({ path: path.join(__dirname, 'chat-fixture.ts') }))
    build.onResolve({ filter: /^(vue-i18n|@vueuse\/core|@antv\/s2|html2canvas|@\/i18n|@\/stores\/assistant|@\/utils\/useEmitt.ts|@\/views\/chat\/component\/index.ts)$/ }, () => ({ path: fixture }))
    build.onResolve({ filter: /\.css$/ }, args => ({ path: args.path, namespace: 'css' }))
    build.onLoad({ filter: /.*/, namespace: 'css' }, () => ({ contents: '', loader: 'js' }))
    build.onResolve({ filter: /\.svg$/ }, args => ({ path: args.path, namespace: 'svg' }))
    build.onLoad({ filter: /.*/, namespace: 'svg' }, () => ({ contents: 'export default {}', loader: 'js' }))
    build.onResolve({ filter: /^@\// }, args => ({ path: path.resolve(__dirname, '../src', args.path.slice(2)) + (path.extname(args.path) ? '' : '.ts') }))
    build.onLoad({ filter: /\.vue$/ }, args => {
      const answer = /ChartAnswer\.vue$/.test(args.path)
      if (!answer && !/(ChartBlock|DisplayChartBlock|ChartComponent)\.vue$/.test(args.path)) return { contents: 'export default {}', loader: 'js' }
      const { descriptor } = parse(fs.readFileSync(args.path, 'utf8'))
      return { contents: compileScript(descriptor, { id: 'result-test', inlineTemplate: !answer }).content, loader: 'ts', resolveDir: path.dirname(args.path) }
    })
  }}],
}).then(() => {
  const run = spawnSync(process.execPath, ['--test', path.join(__dirname, '../node_modules/.cache/result-first-tests.cjs')], { stdio: 'inherit' })
  process.exit(run.status ?? 1)
}).catch(error => { console.error(error); process.exit(1) })
